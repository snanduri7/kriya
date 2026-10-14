"""PLAN-EXTERNAL-PACKAGE-CAPABILITY-001: characterization of how Kriya's production plan validator treats a Planner plan that names an INSTALLED THIRD-PARTY PACKAGE
in a planned file's ``requires_capabilities``.

No model, no network: the Planner answers were recorded verbatim from a real run (tests/fixtures/plan_external_package_capability_001.json) and are replayed through the SAME
parse -> build -> normalise -> ``validate_plan`` chain ``WorkflowController`` runs in enforce mode (kriya/workflow/workflow_controller.py, the structured-planning loop).

These tests CHARACTERIZE current behaviour; they do not assert that behaviour is desirable. Diagnosis (handover/DEMO-V2/FINDINGS.md F2): the FIRST wrong decision is the Planner's: its prompt says
``requires_capabilities`` "names only an exact capability supplied by another planned subtask" and puts ambient tools in ``environment_requirements``, and Kriya supplied the deterministic facts
(the packages occur in requirements.txt). The validator is consistent with that contract.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from kriya.agents.contracts import parse_planner_structured_output
from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.plan_normalization import (
    coalesce_same_file_owners,
    derive_verification_contracts,
    redirect_immutable_test_edits,
    scope_verification_to_requirements,
)
from kriya.workflow.plan_schema import build_engineering_plan_from_planner_output
from kriya.workflow.plan_validation import canonicalize_planned_file_actions, validate_plan
from kriya.workflow.triage import ChangeKind

DATA = json.loads((Path(__file__).parent / "fixtures" / "plan_external_package_capability_001.json").read_text(encoding="utf-8"))
RESPONSES: List[str] = DATA["planner_responses"]
PACKAGE = "tree-sitter-python"


@pytest.fixture
def workspace(tmp_path: Path) -> str:
    (tmp_path / "requirements.txt").write_text(DATA["requirements_txt"])
    return str(tmp_path)


def _validate(plan_text: str, workspace: str) -> Tuple[bool, List[str], List[str]]:
    """The controller's chain, in its order, for one Planner answer."""
    output, issue = parse_planner_structured_output(plan_text)
    assert output is not None, issue
    raw = build_engineering_plan_from_planner_output(output, plan_id="repro", kind=ChangeKind.TASK)
    plan, _ = canonicalize_planned_file_actions(raw, workspace)
    plan, _ = coalesce_same_file_owners(plan)
    plan, _ = redirect_immutable_test_edits(plan, (), lambda rel: os.path.exists(os.path.join(workspace, rel)))
    plan, _ = derive_verification_contracts(plan)
    goal = DATA["goal"]
    plan, _ = scope_verification_to_requirements(plan, goal_requires_runtime_behavior(goal))
    result = asyncio.run(validate_plan(
        plan, workspace_path=workspace, available_tool_names=["compile", "test"], require_model_planned_files=True, require_semantic_contracts=True,
        runtime_verification_required=goal_requires_runtime_behavior(goal)))
    return result.valid, list(result.reason_codes), list(result.errors)


def _variant(index: int, edit) -> str:
    plan = copy.deepcopy(json.loads(RESPONSES[index]))
    edit(plan)
    return json.dumps(plan)


def _s1_file(plan: Dict[str, Any]) -> Dict[str, Any]:
    return plan["subtasks"][0]["planned_files"][0]


@pytest.mark.parametrize("attempt", [0, 1, 2])
def test_each_recorded_planner_answer_is_deterministically_rejected_with_the_codes_the_live_run_logged(workspace, attempt):
    valid, codes, errors = _validate(RESPONSES[attempt], workspace)
    assert valid is False
    assert codes == DATA["live_reason_codes_per_attempt"][attempt]
    assert DATA["repair_feedback_must_fix_line"] in errors  # the exact text the repair prompt carried as MUST FIX


def test_the_violation_is_structurally_repeated_across_every_repair_round_not_merely_the_same_code():
    for text in RESPONSES:
        s1 = json.loads(text)["subtasks"][0]
        assert s1["id"] == "s1" and s1["requires"] == [], "the consumer never declares the capability in requires"
        assert s1["planned_files"][0]["path"] == "outline.py" and s1["planned_files"][0]["requires_capabilities"] == [PACKAGE]


def test_the_plan_is_invalid_only_because_of_that_one_entry(workspace):
    """Removing the entry, or listing the package where the Planner's contract puts ambient requirements, validates: nothing else is wrong with the recorded plan."""
    assert _validate(_variant(2, lambda p: _s1_file(p).update(requires_capabilities=[])), workspace) == (True, [], [])
    assert _validate(_variant(2, lambda p: _s1_file(p).update(requires_capabilities=[], environment_requirements=["python", PACKAGE])), workspace) == (True, [], [])


def test_following_the_repair_feedback_literally_leads_to_a_different_rejection(workspace):
    """The feedback says the consumer 'does not declare that capability in requires'. Doing exactly that (no other change) is rejected again: no subtask provides it.
    This characterizes the dead end a literal reading of the feedback produces; it is an observation about the feedback, not the classification of the first wrong decision."""
    valid, codes, errors = _validate(_variant(2, lambda p: p["subtasks"][0].update(requires=[PACKAGE])), workspace)
    assert valid is False and codes == ["SUBTASK_REQUIREMENT_UNPROVIDED"]
    assert errors == [f"subtask 's1' requires '{PACKAGE}' but no subtask provides it"]


def test_a_genuinely_undeclared_planned_capability_is_still_rejected_whatever_its_name(workspace):
    """The validator rejects an undeclared planned-artifact prerequisite by the declared contract, not by any package name: an invented capability is rejected the same way."""
    valid, codes, _ = _validate(_variant(2, lambda p: _s1_file(p).update(requires_capabilities=["some-invented-capability"])), workspace)
    assert valid is False and codes == ["PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED"]


def test_ambient_tools_named_in_requires_capabilities_are_exempt_today(workspace):
    """The validator's closed exemption set (python, pytest, pip, npm ...): documented current behaviour, unchanged here."""
    assert _validate(_variant(2, lambda p: _s1_file(p).update(requires_capabilities=["python", "pytest"])), workspace) == (True, [], [])


def test_a_capability_supplied_by_a_declared_dependency_is_accepted(workspace):
    """The intended use of requires_capabilities: a capability another planned subtask provides, declared in requires and depends_on."""
    def edit(plan):
        s1, s2 = plan["subtasks"]
        s2["planned_files"][0]["requires_capabilities"] = ["outline-module"]
        assert s2["requires"] == ["outline-module"] and s2["depends_on"] == ["s1"]
        _s1_file(plan)["requires_capabilities"] = []
    assert _validate(_variant(2, edit), workspace) == (True, [], [])


# ---- GENERIC PLAN-REPAIR CONTRACT CLARIFICATION (owner decision 2026-10-14) -----------------------------------------------------------------------
# One generic distinction, no technology-specific text: requires_capabilities is ONLY for a capability another PLANNED SUBTASK supplies; a library, package, runtime or tool the
# project or execution environment supplies is an environment dependency (environment_requirements). The validator's acceptance rules are NOT changed by this work: the tests above
# (and A-E below) pin them; F-G pin the new deterministic repair feedback and the one-sentence contract rule.
from kriya.agents.contracts import ENVIRONMENT_DEPENDENCY_RULE  # noqa: E402 - grouped with the clarification tests
from kriya.workflow.planner_repair import build_structured_plan_repair_prompt, unprovided_capability_facts  # noqa: E402
from kriya.workflow.workflow_controller import (  # noqa: E402
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
    build_authoritative_planner_request,
)

TECHNOLOGY_WORDS = ("tree-sitter", "tree_sitter", "treesitter", "pip", "requirements.txt", "pyproject", "maven", "pom.xml", "npm", "package.json", "gradle", "cargo", "scenario")


@pytest.mark.parametrize("capability", ["numpy", "requests", "lodash", "org.example:some-library", "left-pad", "@scope/pkg", "some_python_module"])
def test_C_no_package_like_name_is_exempt_from_the_undeclared_capability_rule(workspace, capability):
    valid, codes, _ = _validate(_variant(2, lambda p: _s1_file(p).update(requires_capabilities=[capability])), workspace)
    assert valid is False and codes == ["PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED"], capability


@pytest.mark.parametrize("dependency", ["numpy", "lodash", "org.example:some-library", "some_python_module"])
def test_E_any_external_dependency_in_environment_requirements_is_accepted(workspace, dependency):
    edit = lambda p: _s1_file(p).update(requires_capabilities=[], environment_requirements=["python", dependency])  # noqa: E731
    assert _validate(_variant(2, edit), workspace) == (True, [], [])


def _feedback_for(attempt: int, workspace: str) -> str:
    valid, codes, errors = _validate(RESPONSES[attempt], workspace)
    assert valid is False
    return build_structured_plan_repair_prompt(DATA["goal"], RESPONSES[attempt], errors, codes, attempt + 1)


@pytest.mark.parametrize("attempt", [0, 1, 2])
def test_F_the_repair_feedback_for_an_unprovided_capability_states_the_generic_distinction(workspace, attempt):
    feedback = _feedback_for(attempt, workspace)
    assert "UNPROVIDED_CAPABILITY" in feedback
    section = feedback[feedback.index("UNPROVIDED_CAPABILITY"):]
    section = section[:section.index("\n- ", 10)] if "\n- " in section[10:] else section
    # typed facts: the capability, its consumer subtask and file
    assert f'capability="{PACKAGE}"' in section and "subtask=s1" in section and "file=outline.py" in section
    # the decision boundary, in the repository's own terms
    assert "requires_capabilities" in section and "another planned subtask" in section and "environment_requirements" in section
    # the three valid repairs, in order; Kriya does not choose a branch for the model
    assert section.index("1.") < section.index("2.") < section.index("3.")
    assert "provides" in section and "add the missing provider" in section and "supplied by the project or execution environment" in section
    # the dead end the old feedback led into
    assert "SUBTASK_REQUIREMENT_UNPROVIDED" in section


def test_F_a_capability_a_planned_subtask_does_provide_gets_no_unprovided_block(workspace):
    plan = _variant(2, lambda p: (_s1_file(p).update(requires_capabilities=[]), p["subtasks"][1]["planned_files"][0].update(requires_capabilities=["outline-module"])))
    p = json.loads(plan)
    p["subtasks"][1]["requires"] = []  # the consumer forgets the requires edge: a DIFFERENT repair (the existing prerequisite correction), not an unprovided capability
    valid, codes, errors = _validate(json.dumps(p), workspace)
    assert valid is False and "PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED" in codes
    assert unprovided_capability_facts(json.dumps(p)) == []
    assert "UNPROVIDED_CAPABILITY" not in build_structured_plan_repair_prompt(DATA["goal"], json.dumps(p), errors, codes, 1)


def test_F_facts_are_typed_deduplicated_and_skip_exempt_ambient_tools():
    plan = json.loads(RESPONSES[2])
    _s1_file(plan)["requires_capabilities"] = [PACKAGE, PACKAGE, "pytest", "", "other-cap"]
    facts = unprovided_capability_facts(json.dumps(plan))
    assert facts == [{"capability": PACKAGE, "subtask_id": "s1", "planned_file": "outline.py"}, {"capability": "other-cap", "subtask_id": "s1", "planned_file": "outline.py"}]


def test_G_the_new_feedback_carries_no_technology_or_package_specific_text():
    plan = json.loads(RESPONSES[2])
    _s1_file(plan)["requires_capabilities"] = ["zz-synthetic-capability-9"]
    text = build_structured_plan_repair_prompt("Make a thing.", json.dumps(plan), ["x"], ["PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED"], 1)
    block = text[text.index("UNPROVIDED_CAPABILITY"):]
    block = block[:block.index("\n- ", 10)] if "\n- " in block[10:] else block
    for word in TECHNOLOGY_WORDS:
        assert word not in block.lower(), word
    assert 'capability="zz-synthetic-capability-9"' in block  # the only specific text is the capability name taken from the plan itself


def test_the_contract_rule_is_one_concise_generic_sentence_present_wherever_the_dependency_fields_are_taught():
    assert ". " not in ENVIRONMENT_DEPENDENCY_RULE and ENVIRONMENT_DEPENDENCY_RULE.endswith(".") and len(ENVIRONMENT_DEPENDENCY_RULE) < 330  # one sentence
    assert "environment_requirements" in ENVIRONMENT_DEPENDENCY_RULE and "requires_capabilities" in ENVIRONMENT_DEPENDENCY_RULE
    assert not any(word in ENVIRONMENT_DEPENDENCY_RULE.lower() for word in TECHNOLOGY_WORDS)
    assert ENVIRONMENT_DEPENDENCY_RULE in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT  # the structured Planner's system prompt (kriya/workflow/workflow_controller.py)
    assert AUTHORITATIVE_PLANNER_SYSTEM_PROMPT.count(ENVIRONMENT_DEPENDENCY_RULE) == 1
    request = build_authoritative_planner_request("Make a thing.")
    assert request.count(ENVIRONMENT_DEPENDENCY_RULE) == 1  # the user-message protocol is generated independently of the system prompt


def test_the_legacy_planner_agent_prompt_states_the_same_rule():
    from kriya.agents.agent import PlannerAgent
    prompt = PlannerAgent.__new__(PlannerAgent).system_prompt
    assert prompt.count(ENVIRONMENT_DEPENDENCY_RULE) == 1


def test_the_unprovided_capability_block_appears_only_for_the_reason_codes_that_report_it():
    for codes in (["SUBTASK_DEPENDENCY_CYCLE"], ["PLANNED_ARTIFACT_PREREQUISITE_INVALID"], []):
        text = build_structured_plan_repair_prompt(DATA["goal"], RESPONSES[2], ["x"], codes, 1)
        assert "UNPROVIDED_CAPABILITY" not in text, codes
    for codes in (["PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED"], ["SUBTASK_REQUIREMENT_UNPROVIDED"]):
        assert "UNPROVIDED_CAPABILITY" in build_structured_plan_repair_prompt(DATA["goal"], RESPONSES[2], ["x"], codes, 1), codes


def test_an_unparsable_previous_plan_produces_no_block_and_no_exception():
    assert unprovided_capability_facts("not json at all") == [] and unprovided_capability_facts("") == []
    assert "UNPROVIDED_CAPABILITY" not in build_structured_plan_repair_prompt("g", "not json", ["x"], ["PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED"], 1)

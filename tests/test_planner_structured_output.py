"""MA6.3/MA7.8: parse_planner_structured_output (kriya/agents/contracts.py) -
first real direct test coverage for this function (previously only
exercised indirectly via mocks in tests/test_workflow_controller*.py).

Special focus on _self_heal_structured_plan_dict, added 2026-08-24 after a
real live-validation finding (protocol_encoder_java): a single malformed
subtask (execution_method="tool" with no tool_name) used to invalidate
the ENTIRE structured plan, even though every other subtask was fine -
WorkflowController's enforce mode then fell back to the legacy path for a
plan that was almost entirely usable. Every self-heal here is a real,
unambiguous downgrade/removal, never a guess at new information."""

import json

from kriya.agents.contracts import parse_planner_structured_output
from kriya.workflow.plan_schema import ExecutionMethod, VerificationMethodType


def _plan_text(plan_json):
    return f"Some prose plan.\n\n```json\n{json.dumps(plan_json)}\n```"


# --- baseline behavior, not previously covered by any direct test ---

def test_empty_text_returns_none_with_a_reason():
    output, err = parse_planner_structured_output("")
    assert output is None
    assert "empty" in err


def test_no_fenced_json_block_returns_none_with_a_reason():
    output, err = parse_planner_structured_output("just prose, no JSON block anywhere")
    assert output is None
    assert "no structured plan JSON block" in err


def test_malformed_json_returns_none_with_a_reason():
    # brace-balanced (so _FENCED_JSON_BLOCK's regex actually matches it as
    # a candidate) but syntactically invalid JSON inside - a trailing
    # comma before the closing brace
    output, err = parse_planner_structured_output('prose\n```json\n{"subtasks": [],}\n```')
    assert output is None
    assert "did not parse" in err


def test_a_well_formed_plan_parses_cleanly():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s1", "description": "write a", "execution_method": "model", "acceptance_criteria_ids": ["ac1"]},
        ],
        "acceptance_criteria": [{"id": "ac1", "description": "a exists"}],
    }))
    assert err is None
    assert output is not None
    assert len(output.subtasks) == 1
    assert output.subtasks[0].execution_method == ExecutionMethod.MODEL


def test_a_genuinely_unfixable_shape_still_fails_cleanly():
    """Confirms the self-heal pre-pass doesn't mask a REAL problem -
    subtasks not even being a list is not something any of the narrow
    corrections below can or should paper over."""
    output, err = parse_planner_structured_output(_plan_text({"subtasks": "not a list"}))
    assert output is None
    assert err is not None


# --- MA7.8 self-healing: the real live incident, reproduced verbatim ---

def test_tool_subtask_with_no_tool_name_and_no_scope_remains_invalid_for_plan_repair():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s1", "description": "write Protocol.java", "execution_method": "model", "acceptance_criteria_ids": ["ac1"]},
            {"id": "s2", "description": "write ProtocolTest.java", "execution_method": "model",
             "depends_on": ["s1"], "acceptance_criteria_ids": ["ac2"]},
            {"id": "s3", "description": "run tests", "execution_method": "tool",
             "depends_on": ["s2"], "acceptance_criteria_ids": ["ac3"]},
        ],
        "acceptance_criteria": [
            {"id": "ac1", "description": "x"}, {"id": "ac2", "description": "y"}, {"id": "ac3", "description": "z"},
        ],
    }))
    assert output is None
    assert "execution_method=tool but no tool_name" in err


def test_tool_subtask_with_no_tool_name_but_explicit_scope_can_downgrade_to_model():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [{
            "id": "s1", "description": "write scoped file", "execution_method": "tool",
            "planned_files": [{"path": "a.py", "action": "create"}],
            "acceptance_criteria_ids": [],
        }],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output is not None
    assert output.subtasks[0].execution_method == ExecutionMethod.MODEL
    assert output.subtasks[0].planned_files[0].path == "a.py"


def test_model_subtask_with_a_stray_tool_name_has_it_cleared():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s1", "description": "x", "execution_method": "model", "tool_name": "shell", "acceptance_criteria_ids": []},
        ],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output.subtasks[0].execution_method == ExecutionMethod.MODEL
    assert output.subtasks[0].tool_name is None


def test_model_subtask_with_stray_tool_arguments_has_them_cleared():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s1", "description": "x", "execution_method": "model",
             "tool_arguments": {"command": "ls"}, "acceptance_criteria_ids": []},
        ],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output.subtasks[0].tool_arguments == {}


def test_a_genuine_tool_subtask_with_a_real_tool_name_is_left_alone():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s1", "description": "lint", "execution_method": "tool", "tool_name": "lint", "acceptance_criteria_ids": []},
        ],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output.subtasks[0].execution_method == ExecutionMethod.TOOL
    assert output.subtasks[0].tool_name == "lint"


def test_self_dependency_is_removed_not_left_to_fail_validation():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s1", "description": "x", "execution_method": "model", "depends_on": ["s1"], "acceptance_criteria_ids": []},
        ],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output.subtasks[0].depends_on == []


def test_duplicate_depends_on_is_deduplicated():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [
            {"id": "s0", "description": "base", "execution_method": "model", "acceptance_criteria_ids": []},
            {"id": "s1", "description": "x", "execution_method": "model", "depends_on": ["s0", "s0"], "acceptance_criteria_ids": []},
        ],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output.subtasks[1].depends_on == ["s0"]


def test_acceptance_criterion_tool_method_with_no_tool_name_downgrades_to_judgment():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [{"id": "s1", "description": "x", "execution_method": "model", "acceptance_criteria_ids": ["ac1"]}],
        "acceptance_criteria": [{"id": "ac1", "description": "y", "method": "tool"}],
    }))
    assert err is None
    assert output.acceptance_criteria[0].method == VerificationMethodType.JUDGMENT
    assert output.acceptance_criteria[0].tool_name is None


def test_subtask_verification_method_tool_with_no_tool_name_downgrades_to_judgment():
    output, err = parse_planner_structured_output(_plan_text({
        "subtasks": [{
            "id": "s1", "description": "x", "execution_method": "model", "acceptance_criteria_ids": [],
            "verification": [{"type": "tool", "description": "check it"}],
        }],
        "acceptance_criteria": [],
    }))
    assert err is None
    assert output.subtasks[0].verification[0].type == VerificationMethodType.JUDGMENT
    assert output.subtasks[0].verification[0].tool_name is None

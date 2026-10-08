"""PLANNER-REPAIR-VERIFIER-SHAPE-001 (BACKEND-FINAL-CLOSURE-005, measured on P4-T5-r2 jmespath): the planner wrote a
runtime check as verification type "application_runtime" and an acceptance criterion's method the same way; the
schema rejected it (pydantic: "Input should be 'tool' or 'judgment'"), the repair prompt showed only that enum error,
and the next two repairs circled (judgment -> PLAN_VERIFICATION_SCOPE_UNJUSTIFIED) without ever finding the legal
shape (type 'tool', tool_name 'application_runtime'). The repair guidance now names it when the rejected value is a
check's kind. Repository-independent: the prompt builder alone."""
from kriya.workflow.planner_repair import build_structured_plan_repair_prompt

PYDANTIC_ERROR = (
    "structured plan parse failed: structured plan JSON block failed schema validation: 2 validation errors for "
    "PlannerStructuredOutput\nsubtasks.3.verification.0.type\n  Input should be 'tool' or 'judgment' [type=enum, "
    "input_value='application_runtime', input_type=str]\n    For further information visit "
    "https://errors.pydantic.dev/2.13/v/enum\nacceptance_criteria.3.method\n  Input should be 'tool' or 'judgment' "
    "[type=enum, input_value='application_runtime', input_type=str]\n"
)


def _prompt(errors):
    return build_structured_plan_repair_prompt("goal", "{}", errors, ["STRUCTURED_PLAN_SCHEMA_INVALID"], 1)


def test_a_check_kind_used_as_a_type_gets_the_legal_shape_named():
    prompt = _prompt([PYDANTIC_ERROR])
    assert "type 'tool' and put the kind in tool_name" in prompt
    assert "application_runtime" in prompt and "requires_runtime_execution" in prompt
    assert "method 'tool' with that tool_name" in prompt


def test_other_schema_errors_keep_the_generic_correction_only():
    generic = ("structured plan parse failed: 1 validation error for PlannerStructuredOutput\nsubtasks.0.verification.0\n"
               "  Input should be a valid dictionary [type=dict_type, input_value='compile', input_type=str]\n")
    prompt = _prompt([generic])
    assert "never use a string verification item" in prompt
    assert "put the kind in tool_name" not in prompt
    # a kind that is not a check kind is not mapped either
    assert "put the kind in tool_name" not in _prompt([PYDANTIC_ERROR.replace("application_runtime", "manual")])

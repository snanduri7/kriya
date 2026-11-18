"""PLANNER-SCHEMA-CONSTRAINED-OUTPUT-001 (owner ruling 2026-11-18): the enforce Planner's canonical output schema travels to a runtime that enforces it at decode time.

MEASURED (Stage B run 20261118T085918-19ebb3f8, canonical main c0dfa38, production profile, qualified primary on the native Ollama runtime): the Planner request went out
with ``"format": "json"``; the model answered ``execution_method: "verification"`` (legal: ``model`` | ``tool``), the exact validation error was fed back twice and the
answer repeated it - STRUCTURED_PLAN_SCHEMA_INVALID / STRUCTURED_PLAN_REPAIR_EXHAUSTED, no candidate. The three sealed responses are two distinct documents (the third is
byte-identical to the second) and are replayed here from ``tests/fixtures/planner_schema_constrained_001``.

The contract these tests pin: ``plan_schema.planner_output_json_schema`` IS ``PlannerStructuredOutput``'s own schema (one owner, never a copy); the two enforce Planner
calls pass it; it reaches the provider as ``format`` only for a candidate whose runtime declares decode-time enforcement, asked per candidate model; every other role and
every non-enforcing runtime keeps its exact request; ``schema_constrained_json`` measures the enforcement on the exact runtime and the planner role requires it there
(qualification policy /10, every /9 record STALE).
"""
import ast
import asyncio
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_prd014_model_qualification import MODEL, FakeLLM, _fp, _record, _result, _settings, run
from test_provider_contract_native import _config, _NativeServer

from kriya.agents import agent as agent_module
from kriya.agents.agent import PlannerAgent, call_with_escalation
from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelQualificationConfig, QualificationCasesConfig
from kriya.core import model_qualification as mq
from kriya.core.completion import CompletionStatus
from kriya.core.llm import LLMClient
from kriya.workflow.plan_schema import ExecutionMethod, PlannerStructuredOutput, planner_output_json_schema

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "planner_schema_constrained_001"
REJECTED = sorted(FIXTURES.glob("rejected_stage_b_run1_*.json"))
ACCEPTED = sorted(FIXTURES.glob("accepted_stage_a_run1_*.json"))


@pytest.fixture
def native_server():
    server = _NativeServer()
    yield server
    server.close()


# ---------------------------------------------------------------- one schema owner

def test_the_planner_schema_is_the_validating_models_own_schema_never_a_copy():
    schema = planner_output_json_schema()
    assert schema == PlannerStructuredOutput.model_json_schema()
    assert schema["$defs"]["ExecutionMethod"]["enum"] == [member.value for member in ExecutionMethod] == ["model", "tool"]
    assert schema["$defs"]["Subtask"]["properties"]["execution_method"] == {"$ref": "#/$defs/ExecutionMethod"}


# ---------------------------------------------------------------- the three sealed rejected answers, and a legal plan

def test_the_fixtures_are_the_sealed_answers():
    assert len(REJECTED) == 2 and len(ACCEPTED) == 1      # three model.response records; the second and third were the same bytes


@pytest.mark.parametrize("path", REJECTED, ids=lambda p: p.name)
def test_each_sealed_rejected_answer_is_outside_the_constrained_contract(path):
    plan = json.loads(path.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(planner_output_json_schema()).iter_errors(plan))
    assert errors, "the sealed answer must not be producible under the schema"
    [error] = errors                                        # the ONE defect the run reported, nothing else
    assert list(error.absolute_path)[0] == "subtasks" and list(error.absolute_path)[-1] == "execution_method"
    assert error.instance == "verification" and error.validator == "enum" and error.validator_value == ["model", "tool"]
    with pytest.raises(ValidationError) as refused:         # the same refusal the run recorded (pydantic, the answer's validator)
        PlannerStructuredOutput.model_validate(plan)
    assert "execution_method" in str(refused.value) and "'model' or 'tool'" in str(refused.value)


@pytest.mark.parametrize("method", ["model", "tool"])
def test_the_same_answers_with_a_legal_method_are_inside_the_schema(method):
    for path in REJECTED:
        plan = json.loads(path.read_text(encoding="utf-8"))
        for subtask in plan["subtasks"]:
            if subtask["execution_method"] == "verification":
                subtask["execution_method"] = method
        assert not list(jsonschema.Draft202012Validator(planner_output_json_schema()).iter_errors(plan))


def test_a_plan_the_product_accepted_stays_inside_the_schema():
    plan = json.loads(ACCEPTED[0].read_text(encoding="utf-8"))
    assert not list(jsonschema.Draft202012Validator(planner_output_json_schema()).iter_errors(plan))
    assert {subtask.execution_method for subtask in PlannerStructuredOutput.model_validate(plan).subtasks} == {ExecutionMethod.MODEL}


# ---------------------------------------------------------------- on the wire: the exact schema reaches the native provider

def _planner(config):
    llm = LLMClient(config)
    return PlannerAgent("planner", llm), llm


async def _plan(config, **kwargs):
    planner, llm = _planner(config)
    try:
        return await planner.run("plan the goal", system_prompt_override="You are the structured Planner.", json_mode=True, **kwargs)
    finally:
        await llm.aclose()


def test_the_exact_canonical_schema_travels_to_ollama_as_format(native_server):
    native_server.reply["message"]["content"] = '{"subtasks": []}'
    answer = asyncio.run(_plan(_config(native_server.url), response_schema=planner_output_json_schema()))
    assert answer == '{"subtasks": []}'
    [(path, body)] = native_server.requests
    assert path == "/api/chat"
    assert body["format"] == PlannerStructuredOutput.model_json_schema()       # the whole schema, unmodified - not "json", not a subset
    assert body["truncate"] is False and body["messages"][0]["content"] == "You are the structured Planner."


def test_a_planner_call_without_a_schema_keeps_plain_json_mode(native_server):
    native_server.reply["message"]["content"] = "{}"
    asyncio.run(_plan(_config(native_server.url)))
    [(_path, body)] = native_server.requests
    assert body["format"] == "json"


def test_the_client_passes_the_schema_through_complete_and_never_invents_one(native_server):
    native_server.reply["message"]["content"] = "{}"

    async def calls():
        llm = LLMClient(_config(native_server.url))
        try:
            await llm.complete("s", "u", json_mode=True, response_schema={"type": "object"})
            await llm.complete("s", "u", json_mode=True)
            await llm.complete("s", "u")
        finally:
            await llm.aclose()
    asyncio.run(calls())
    formats = [body.get("format", "<absent>") for _path, body in native_server.requests]
    assert formats == [{"type": "object"}, "json", "<absent>"]


def test_only_a_runtime_that_declares_enforcement_says_it_constrains():
    native = _config("http://127.0.0.1:1/v1")
    native.llm_chain = [FallbackModelConfig(model="other:1", base_url="http://127.0.0.1:1/v1", api_key="k")]      # default runtime
    llm = LLMClient(native)
    assert llm.constrains_json_schema() is True and llm.constrains_json_schema("m:1") is True
    assert llm.constrains_json_schema("other:1") is False
    assert LLMClient(AppConfig()).constrains_json_schema() is False
    assert mq.runtime_constrains_json_schema(native, "m:1") is True and mq.runtime_constrains_json_schema(native, "other:1") is False
    assert mq.runtime_constrains_json_schema(AppConfig(), AppConfig().llm.model) is False


# ---------------------------------------------------------------- role selection: per candidate, never approximated

class _RecordingLLM:
    """A scripted client: which models constrain, which calls raise, and exactly what each call was sent."""

    model = "primary:1"
    config = AppConfig()

    def __init__(self, constraining=(), failing=()):
        self.constraining, self.failing, self.calls = set(constraining), set(failing), []

    def constrains_json_schema(self, model=None):
        return (model or self.model) in self.constraining

    async def complete(self, system_prompt, user_prompt, **kwargs):
        model = kwargs.get("model_override") or self.model
        self.calls.append((model, kwargs.get("response_schema", "<absent>")))
        if model in self.failing:
            raise RuntimeError("endpoint down")
        return "{}"


CHAIN = FallbackModelConfig(model="chain:1", base_url="http://127.0.0.1:1/v1", api_key="k")
SCHEMA = {"type": "object"}


@pytest.mark.parametrize("constraining, expected", [
    ({"primary:1", "chain:1"}, [("primary:1", SCHEMA), ("chain:1", SCHEMA)]),
    ({"primary:1"}, [("primary:1", SCHEMA), ("chain:1", "<absent>")]),
    ({"chain:1"}, [("primary:1", "<absent>"), ("chain:1", SCHEMA)]),
    (set(), [("primary:1", "<absent>"), ("chain:1", "<absent>")]),
])
def test_each_candidate_gets_the_schema_only_when_its_own_runtime_enforces_it(constraining, expected):
    llm = _RecordingLLM(constraining=constraining, failing={"primary:1"})
    asyncio.run(call_with_escalation(llm, "s", "u", [None, CHAIN], json_mode=True, response_schema=SCHEMA))
    assert llm.calls == expected


def test_no_schema_is_sent_when_the_caller_supplies_none_whatever_the_runtime():
    llm = _RecordingLLM(constraining={"primary:1", "chain:1"}, failing={"primary:1"})
    asyncio.run(call_with_escalation(llm, "s", "u", [None, CHAIN], json_mode=True))
    assert llm.calls == [("primary:1", "<absent>"), ("chain:1", "<absent>")]


def test_a_client_that_cannot_say_it_enforces_is_never_sent_a_schema():
    class Silent(_RecordingLLM):
        constrains_json_schema = None

    class Vague(_RecordingLLM):
        def constrains_json_schema(self, model=None):
            return "yes"                                    # truthy is not the declared fact

    for llm in (Silent(), Vague()):
        asyncio.run(call_with_escalation(llm, "s", "u", [None], json_mode=True, response_schema=SCHEMA))
        assert llm.calls == [("primary:1", "<absent>")]
    assert agent_module._schema_request(_RecordingLLM(constraining={"primary:1"}), None, SCHEMA) == {"response_schema": SCHEMA}


# ---------------------------------------------------------------- the two enforce Planner requests, and only they

def _planner_run_calls(relative):
    tree = ast.parse((REPO / relative).read_text(encoding="utf-8"))
    return [call for call in ast.walk(tree) if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "run"
            and isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "planner" or
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "run"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "planner"]


def test_both_enforce_planner_requests_send_the_canonical_schema():
    calls = _planner_run_calls("kriya/workflow/enforce_loop.py")
    assert len(calls) == 2                                  # the first request and the bounded repair
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert isinstance(keywords["json_mode"], ast.Constant) and keywords["json_mode"].value is True
        schema = keywords["response_schema"]
        assert isinstance(schema, ast.Call) and isinstance(schema.func, ast.Name) and schema.func.id == "planner_output_json_schema" and not schema.args


@pytest.mark.parametrize("relative", ["kriya/workflow/stage_planning.py", "kriya/workflow/shadow_mode.py"])
def test_the_prose_and_shadow_planner_requests_are_unchanged(relative):
    calls = _planner_run_calls(relative)
    assert calls and all("response_schema" not in {keyword.arg for keyword in call.keywords} for call in calls)


# ---------------------------------------------------------------- qualification: the enforcement is measured on the exact runtime

class _ConstrainingFakeLLM(FakeLLM):
    def __init__(self, *results, constrains=True):
        super().__init__(*results)
        self.constrains = constrains

    def constrains_json_schema(self, model=None):
        return self.constrains


def _case(content, constrains=True, **result):
    llm = _ConstrainingFakeLLM(_result(content, **result), constrains=constrains)
    return run(mq.case_schema_constrained_json, llm, {}), llm


@pytest.mark.parametrize("content", ['{"execution_method": "model"}', '{"execution_method": "tool"}'])
def test_an_answer_held_inside_the_closed_set_passes(content):
    result, llm = _case(content)
    assert result.status == mq.PASS
    [call] = llm.calls
    assert call["response_schema"] == mq.SCHEMA_CASE_SCHEMA and call["json_mode"] is True and call["model_override"] == MODEL
    assert mq.SCHEMA_CASE_FORBIDDEN in call["user"]        # the prompt asks for the value the schema forbids
    assert mq.SCHEMA_CASE_SCHEMA["properties"]["execution_method"]["enum"] == ["model", "tool"] and mq.SCHEMA_CASE_FORBIDDEN == "verification"


@pytest.mark.parametrize("content", ['{"execution_method": "verification"}', '{"execution_method": "model", "note": "x"}', '{}', '["model"]', "model", ""])
def test_every_answer_outside_the_schema_fails(content):
    result, _llm = _case(content)
    assert result.status == mq.FAIL


def test_a_truncated_answer_never_passes_even_when_its_text_conforms():
    result, _llm = _case('{"execution_method": "model"}', status=CompletionStatus.OUTPUT_TRUNCATED)
    assert result.status == mq.FAIL


def test_a_runtime_that_does_not_enforce_is_unavailable_and_is_sent_nothing():
    result, llm = _case('{"execution_method": "model"}', constrains=False)
    assert result.status == mq.UNAVAILABLE and llm.calls == []


def test_the_planner_role_requires_the_capability_exactly_where_the_planner_sends_a_schema():
    native = _config("http://127.0.0.1:1/v1")
    assert "schema_constrained_json" in mq.CAPABILITIES
    assert "schema_constrained_json" in mq.required_capabilities(native, "planner", "m:1")
    for role in mq.ROLES:
        if role != "planner":
            assert "schema_constrained_json" not in mq.required_capabilities(native, role, "m:1"), role
        assert "schema_constrained_json" not in mq.required_capabilities(AppConfig(), role, AppConfig().llm.model), role


def test_a_planner_binding_that_fails_the_case_is_not_qualified_and_one_that_passes_is():
    native = _config("http://127.0.0.1:1/v1")
    required = mq.required_capabilities(native, "planner", "m:1")
    failed = _record({"schema_constrained_json": mq.FAIL})
    assert mq.assess(_fp(), required, settings=_settings(), record=failed).status != mq.QUALIFIED
    assert mq.assess(_fp(), required, settings=_settings(), record=_record()).status == mq.QUALIFIED


def test_the_policy_version_moved_so_every_earlier_record_is_stale():
    assert mq.QUALIFICATION_POLICY_VERSION == "kriya-qualification/10"
    earlier = _record()
    earlier["policy_version"] = "kriya-qualification/9"
    assert mq.assess(_fp(), ("plain_completion",), settings=_settings(), record=earlier).status == mq.STALE


def test_the_case_adds_no_config_field_and_shares_the_structured_json_budget():
    assert "schema_constrained_json" not in QualificationCasesConfig.model_fields
    assert "schema_constrained_json" not in json.dumps(ModelQualificationConfig().model_dump(mode="json"))
    result, llm = _case('{"execution_method": "model"}')
    assert [call["max_tokens_override"] for call in llm.calls] == [ModelQualificationConfig().cases.structured_json.max_tokens]
    assert result.evidence["policy"]["source"] == "model_qualification.cases.structured_json.max_tokens"

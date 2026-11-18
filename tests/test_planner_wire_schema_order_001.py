"""PLANNER-WIRE-SCHEMA-ORDER-001 (owner ruling 2026-11-18): the Planner's wire schema and the prompt's example share ONE canonical ordered shape.

MEASURED (Stage B run 20261118T102702-eb370f85, canonical main e350b7e, production profile, qualified primary on the native runtime): STRUCTURED_PLAN_EMPTY three
times, the answers ``global_invariants`` + ``integration_relationships`` and no ``subtasks`` key; no candidate. MEASURED on the same pin with a four-key probe
(``~/kriya-stage-b/planner-fix/order_rule_probe_run1.json``): a schema-enforcing decoder writes an object's required keys first in declared order, then its optional
keys in declared order, and never returns to an optional key it has passed. The schema generated from the model classes declared ``subtasks`` before
``global_invariants`` and required neither; the prompt's example begins with ``global_invariants``.

``_written_order_problems`` below is that measured rule as a deterministic acceptor (the 949bdf8 tests validated answers as order-blind JSON-schema instances and
could not see the defect). The sealed answers are replayed from ``tests/fixtures/planner_schema_constrained_001``.
"""
import ast
import asyncio
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_provider_contract_native import _config, _NativeServer

from kriya.agents.agent import PlannerAgent
from kriya.agents.contracts import parse_planner_structured_output
from kriya.core.llm import LLMClient
from kriya.workflow import plan_schema, planning_request
from kriya.workflow.plan_schema import (
    PLANNER_CANONICAL_KEY_ORDER,
    PLANNER_WIRE_REQUIRED,
    AcceptanceCriterion,
    EngineeringPlan,
    GlobalInvariant,
    IntegrationRelationship,
    MutationTarget,
    PlannedFile,
    PlannerStructuredOutput,
    PlannerWireShapeError,
    Subtask,
    VerificationMethod,
    build_engineering_plan_from_planner_output,
    order_planner_object,
    planner_output_json_schema,
    render_planner_example,
)
from kriya.workflow.planning_request import (
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
    PLANNER_EXAMPLE_PLAN,
    PLANNER_EXAMPLE_RELATIONSHIP,
)
from kriya.workflow.triage import ChangeKind

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "planner_schema_constrained_001"
RUN1_ILLEGAL_ENUM = sorted(FIXTURES.glob("rejected_stage_b_run1_*.json"))
RUN2_EMPTY = sorted(FIXTURES.glob("rejected_stage_b_run2_*.json"))
ACCEPTED = sorted(FIXTURES.glob("accepted_stage_a_run1_*.json"))
MODELS = {
    "PlannerStructuredOutput": PlannerStructuredOutput, "GlobalInvariant": GlobalInvariant, "Subtask": Subtask, "PlannedFile": PlannedFile,
    "VerificationMethod": VerificationMethod, "AcceptanceCriterion": AcceptanceCriterion, "IntegrationRelationship": IntegrationRelationship,
    "MutationTarget": MutationTarget,
}
# The prompt's bytes at e350b7e, before its two example objects were rendered from the canonical shape: the correction changes the schema, not the prompt.
PROMPT_SHA256_BEFORE_THE_CORRECTION = "ddb10c53d6385de488a00f72f6f240e6f79bd23621ab1a25488e3708a8f0034c"


# ---------------------------------------------------------------- the measured decoder rule, as an acceptor

class _Pairs(list):
    """A JSON object as its (key, value) pairs in the order they were written."""


def _load_ordered(text):
    return json.loads(text, object_pairs_hook=_Pairs)


def _object_schema(fragment, root):
    """The object schema (one with ``properties``) a fragment denotes, following ``$ref`` and the object arm of a union; None for anything else."""
    if not isinstance(fragment, dict):
        return None
    if "$ref" in fragment:
        return _object_schema(root["$defs"][fragment["$ref"].rsplit("/", 1)[-1]], root)
    if "properties" in fragment:
        return fragment
    for arm in fragment.get("anyOf", []):
        found = _object_schema(arm, root)
        if found is not None:
            return found
    return None


def _written_order_problems(root, fragment, value, where="$"):
    """Why a schema-enforcing decoder could NOT have written ``value`` for ``fragment``: a required key missing, a key the object does not have, or keys out of
    the only order it writes (required keys in declared order, then optional keys in declared order). Empty when it could."""
    problems = []
    if isinstance(value, _Pairs):
        schema = _object_schema(fragment, root)
        if schema is None:
            return problems                                  # a free-form object (tool_arguments, ownership_justification): no declared keys
        declared = list(schema["properties"])
        required = [key for key in declared if key in schema.get("required", [])]
        writable = required + [key for key in declared if key not in required]
        written = [key for key, _ in value]
        problems += [f"{where}: required key {key!r} is missing" for key in required if key not in written]
        problems += [f"{where}: {key!r} is not a key of this object" for key in written if key not in writable]
        positions = [writable.index(key) for key in written if key in writable]
        if positions != sorted(set(positions)):
            problems.append(f"{where}: keys {written} cannot be written in this order (the decoder's order is {writable})")
        for key, item in value:
            if key in schema["properties"]:
                problems += _written_order_problems(root, schema["properties"][key], item, f"{where}.{key}")
    elif isinstance(value, list):
        items = fragment.get("items") if isinstance(fragment, dict) else None
        for arm in (fragment.get("anyOf", []) if isinstance(fragment, dict) and items is None else []):
            items = items or arm.get("items")
        for index, item in enumerate(value):
            problems += _written_order_problems(root, items or {}, item, f"{where}[{index}]")
    return problems


def _writable(schema, text, fragment=None):
    return _written_order_problems(schema, schema if fragment is None else fragment, _load_ordered(text))


def test_the_acceptor_is_the_measured_rule():
    # ~/kriya-stage-b/planner-fix/order_rule_probe_run1.json: declared a, b, c, d with b and d required; asked for a, b, c, d; the pin wrote b, d, a, c.
    probe = {"type": "object", "properties": {key: {"type": "string"} for key in "abcd"}, "required": ["b", "d"]}
    assert _writable(probe, '{"b": "2", "d": "4", "a": "1", "c": "3"}') == []
    assert _writable(probe, '{"b": "2", "d": "4", "c": "3"}') == []                    # an optional key may be skipped
    assert _writable(probe, '{"a": "1", "b": "2", "c": "3", "d": "4"}')                # declared order is NOT what it writes
    assert _writable(probe, '{"b": "2", "d": "4", "c": "3", "a": "1"}')                # never back to a passed optional key
    assert _writable(probe, '{"b": "2", "a": "1"}')                                    # a required key cannot be left out
    assert _writable(probe, '{"b": "2", "d": "4", "e": "5"}')


# ---------------------------------------------------------------- the original symptom, replayed against the schema that produced it

def test_the_fixtures_are_the_sealed_answers():
    assert len(RUN1_ILLEGAL_ENUM) == 2 and len(RUN2_EMPTY) == 2 and len(ACCEPTED) == 1
    for path in RUN2_EMPTY:                                 # the blob store names each answer by its sha256
        assert hashlib.sha256(path.read_bytes()).hexdigest().startswith(path.stem.rsplit("_", 1)[-1])


def test_the_schema_generated_from_the_models_could_not_write_the_shape_the_prompt_shows():
    """The defect at e350b7e: under the generated schema the run 2 answer is writable and the prompt's own example is not."""
    generated = PlannerStructuredOutput.model_json_schema()
    example = render_planner_example("PlannerStructuredOutput", PLANNER_EXAMPLE_PLAN)
    assert example in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert any("cannot be written in this order" in problem and problem.startswith("$:") for problem in _writable(generated, example))
    for path in RUN2_EMPTY:
        assert _writable(generated, path.read_text(encoding="utf-8")) == []
    assert _writable(generated, ACCEPTED[0].read_text(encoding="utf-8"))               # nor the plan the product accepted in stage A


@pytest.mark.parametrize("path", RUN2_EMPTY, ids=lambda p: p.name)
def test_the_run_2_answer_cannot_be_written_under_the_wire_schema(path):
    text = path.read_text(encoding="utf-8")
    assert list(json.loads(text)) == ["global_invariants", "integration_relationships"]
    schema = planner_output_json_schema()
    assert "$: required key 'subtasks' is missing" in _writable(schema, text)
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(json.loads(text)))
    assert [(list(error.absolute_path), error.validator, error.message) for error in errors] == [([], "required", "'subtasks' is a required property")]
    # the parser keeps its own contract: the answer still parses, to a plan with no subtask, and no plan is built from it (STRUCTURED_PLAN_EMPTY)
    output, reason = parse_planner_structured_output(text)
    assert reason is None and output is not None and output.subtasks == [] and len(output.global_invariants) == 4
    assert build_engineering_plan_from_planner_output(output, plan_id="p", kind=ChangeKind.TASK) is None


# ---------------------------------------------------------------- legal plans stay writable and accepted

def test_the_prompt_example_can_be_written_under_the_wire_schema():
    schema = planner_output_json_schema()
    assert _writable(schema, render_planner_example("PlannerStructuredOutput", PLANNER_EXAMPLE_PLAN)) == []
    relationship = render_planner_example("IntegrationRelationship", PLANNER_EXAMPLE_RELATIONSHIP)
    assert _writable(schema, relationship, schema["$defs"]["IntegrationRelationship"]) == []


def test_the_plan_the_product_accepted_can_be_written_exactly_as_it_was():
    text = ACCEPTED[0].read_text(encoding="utf-8")
    schema = planner_output_json_schema()
    assert _writable(schema, text) == []                    # byte for byte the stage A answer, key order included
    plan = json.loads(text)
    assert not list(jsonschema.Draft202012Validator(schema).iter_errors(plan))
    output, reason = parse_planner_structured_output(text)
    assert reason is None and len(output.subtasks) == len(plan["subtasks"]) > 0
    assert build_engineering_plan_from_planner_output(output, plan_id="p", kind=ChangeKind.TASK) is not None


def test_a_plan_using_every_key_can_be_written_in_canonical_order():
    full = {
        "integration_relationships": [{
            "selected_alternative": "a", "alternatives": ["a", "b"], "acceptance_condition": "c", "participating_artifacts": ["A.java"],
            "relationship_statement": "s2 uses s1", "consumer_subtask_ids": ["s2"], "producer_subtask_ids": ["s1"], "kind": "uses", "id": "ir1",
        }],
        "refactor_baseline": None, "extension_points": [], "acceptance_criteria": [{"tool_name": "test", "method": "tool", "description": "d", "id": "ac1"}],
        "subtasks": [
            {"mutation_targets": [{"requirement_ids": ["REQ-1"], "action": "modify", "file": "A.java", "target_id": "t1"}],
             "ownership_justification": {"A.java": "new owner"}, "requirement_ids": ["REQ-1"], "acceptance_criteria_ids": ["ac1"],
             "verification": [{"requires_runtime_execution": False, "verifier_kind": "compile", "tool_name": "compile", "description": "d", "type": "tool"}],
             "relevant_global_invariant_ids": ["gi1"], "requires": [], "provides": ["a.api"],
             "planned_files": [{"preserved_references": [], "requires_capabilities": [], "environment_requirements": ["java"], "reason": "r",
                                "action": "create", "path": "A.java"}],
             "depends_on": [], "execution_role": "implementation", "execution_method": "model", "description": "d", "id": "s1"},
            {"verification": [], "planned_files": [], "depends_on": ["s1"], "tool_arguments": {"pattern": "x", "a": 1}, "tool_name": "search",
             "execution_method": "tool", "description": "d", "id": "s2"},
        ],
        "global_invariants": [{"statement": "s", "id": "gi1"}],
    }
    schema = planner_output_json_schema()
    assert _writable(schema, json.dumps(full))               # as given (every object reversed) it cannot be written
    ordered = order_planner_object("PlannerStructuredOutput", full)
    assert _writable(schema, json.dumps(ordered)) == []
    assert ordered == full                                   # the same content, only the order moved (dict equality ignores order)
    assert list(ordered["subtasks"][1]["tool_arguments"]) == ["pattern", "a"]          # a free-form object keeps the order it was given
    assert not list(jsonschema.Draft202012Validator(schema).iter_errors(ordered))
    assert PlannerStructuredOutput.model_validate(ordered) == PlannerStructuredOutput.model_validate(full)
    assert {key for name in MODELS for key in PLANNER_CANONICAL_KEY_ORDER[name]} == _keys_used(_load_ordered(json.dumps(ordered)), schema, schema)


def _keys_used(value, fragment, root):
    used = set()
    if isinstance(value, _Pairs):
        schema = _object_schema(fragment, root)
        if schema is not None:
            for key, item in value:
                used |= {key} | _keys_used(item, schema["properties"][key], root)
    elif isinstance(value, list):
        items = fragment.get("items") or next((arm["items"] for arm in fragment.get("anyOf", []) if "items" in arm), {})
        for item in value:
            used |= _keys_used(item, items, root)
    return used


@pytest.mark.parametrize("path", RUN1_ILLEGAL_ENUM, ids=lambda p: p.name)
def test_the_run_1_answers_stay_outside_the_wire_schema_for_their_one_illegal_value(path):
    plan = json.loads(path.read_text(encoding="utf-8"))
    [error] = jsonschema.Draft202012Validator(planner_output_json_schema()).iter_errors(plan)
    assert list(error.absolute_path)[-1] == "execution_method" and error.validator == "enum" and error.instance == "verification"


# ---------------------------------------------------------------- one canonical shape: the structure both sides take their order from

def test_the_canonical_order_lists_exactly_the_keys_of_every_planner_object():
    schema = planner_output_json_schema()
    objects = {schema["title"]: schema, **{name: definition for name, definition in schema["$defs"].items() if "properties" in definition}}
    assert set(objects) == set(PLANNER_CANONICAL_KEY_ORDER) == set(MODELS)
    for name, model in MODELS.items():
        assert set(PLANNER_CANONICAL_KEY_ORDER[name]) == set(model.model_fields), name
        assert tuple(objects[name]["properties"]) == PLANNER_CANONICAL_KEY_ORDER[name], name


def test_in_every_object_the_required_keys_are_the_first_keys_in_the_same_order():
    schema = planner_output_json_schema()
    for name in MODELS:
        wired = schema if name == schema["title"] else schema["$defs"][name]
        required = wired["required"]
        assert required == list(wired["properties"])[:len(required)], name


def test_only_the_order_and_the_contract_mandatory_keys_differ_from_the_generated_schema():
    generated, schema = PlannerStructuredOutput.model_json_schema(), planner_output_json_schema()
    assert set(schema["$defs"]) == set(generated["$defs"])
    extra = {}
    for name in MODELS:
        ours = schema if name == schema["title"] else schema["$defs"][name]
        theirs = generated if name == generated["title"] else generated["$defs"][name]
        assert ours["properties"] == theirs["properties"], name                        # every type, enum, default and reference is the generated one
        assert ({key: value for key, value in ours.items() if key not in ("properties", "required", "$defs")}
                == {key: value for key, value in theirs.items() if key not in ("properties", "required", "$defs")}), name
        assert set(theirs.get("required", [])) <= set(ours["required"]), name
        added = set(ours["required"]) - set(theirs.get("required", []))
        if added:
            extra[name] = added
    assert extra == {name: set(keys) for name, keys in PLANNER_WIRE_REQUIRED.items()} == {
        "PlannerStructuredOutput": {"global_invariants", "subtasks"}, "IntegrationRelationship": {"producer_subtask_ids", "consumer_subtask_ids"},
    }
    for name, definition in generated["$defs"].items():       # the closed vocabularies are untouched
        if "properties" not in definition:
            assert schema["$defs"][name] == definition


def test_each_key_the_wire_requires_is_already_mandatory_in_the_plan_contract():
    with pytest.raises(ValidationError, match="at least one subtask"):
        EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[])
    assert build_engineering_plan_from_planner_output(PlannerStructuredOutput(), plan_id="p", kind=ChangeKind.TASK) is None
    for key in ("producer_subtask_ids", "consumer_subtask_ids"):
        with pytest.raises(ValidationError, match="at least one producer/consumer"):
            IntegrationRelationship(**{"id": "ir1", "kind": "uses", "relationship_statement": "s", "producer_subtask_ids": ["s1"], "consumer_subtask_ids": ["s2"],
                                       key: []})
    # every authoritative validation of a plan requires non-empty global_invariants (PLAN_GLOBAL_INVARIANTS_MISSING)
    tree = ast.parse((REPO / "kriya/workflow/enforce_loop.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "validate_plan"]
    assert len(calls) == 4
    for call in calls:
        [keyword] = [kw for kw in call.keywords if kw.arg == "require_semantic_contracts"]
        assert isinstance(keyword.value, ast.Constant) and keyword.value.value is True
    source = (REPO / "kriya/workflow/plan_validation.py").read_text(encoding="utf-8")
    assert 'if require_semantic_contracts and not plan.global_invariants:' in source and '"PLAN_GLOBAL_INVARIANTS_MISSING"' in source


def test_the_parsing_model_is_unchanged_a_missing_key_still_parses():
    assert PlannerStructuredOutput.model_json_schema().get("required") is None
    assert PlannerStructuredOutput.model_validate({}).subtasks == []


# ---------------------------------------------------------------- the prompt takes its order from the same shape

def _json_objects_in(text):
    decoder, found, index = json.JSONDecoder(object_pairs_hook=_Pairs), [], 0
    while True:
        index = text.find("{", index)
        if index < 0:
            return found
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index += 1
            continue
        found.append((text[index:end], value))
        index = end


def test_the_prompt_is_byte_for_byte_the_prompt_before_the_correction():
    assert hashlib.sha256(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT.encode("utf-8")).hexdigest() == PROMPT_SHA256_BEFORE_THE_CORRECTION


def test_every_object_the_prompt_shows_is_rendered_from_the_canonical_shape_and_writable():
    schema = planner_output_json_schema()
    shown = _json_objects_in(AUTHORITATIVE_PLANNER_SYSTEM_PROMPT)
    plan_text = render_planner_example("PlannerStructuredOutput", PLANNER_EXAMPLE_PLAN)
    relationship_text = render_planner_example("IntegrationRelationship", PLANNER_EXAMPLE_RELATIONSHIP)
    assert [text for text, _ in shown] == [plan_text, relationship_text]              # exactly these two objects, nothing hand-ordered beside them
    assert _written_order_problems(schema, schema, shown[0][1]) == []
    assert _written_order_problems(schema, schema["$defs"]["IntegrationRelationship"], shown[1][1]) == []
    assert [key for key, _ in shown[0][1]] == [key for key in PLANNER_CANONICAL_KEY_ORDER["PlannerStructuredOutput"] if key in PLANNER_EXAMPLE_PLAN]
    assert [key for key, _ in shown[0][1]][:2] == ["global_invariants", "subtasks"]


def _reversed_keys(value):
    if isinstance(value, dict):
        return {key: _reversed_keys(value[key]) for key in reversed(list(value))}
    return [_reversed_keys(item) for item in value] if isinstance(value, list) else value


def test_the_example_values_carry_no_order_of_their_own():
    for name, value in (("PlannerStructuredOutput", PLANNER_EXAMPLE_PLAN), ("IntegrationRelationship", PLANNER_EXAMPLE_RELATIONSHIP)):
        flipped = _reversed_keys(value)
        assert list(flipped) != list(value) and flipped == value
        assert render_planner_example(name, flipped) == render_planner_example(name, value)
    source = (REPO / "kriya/workflow/planning_request.py").read_text(encoding="utf-8")
    prompt = source[source.index("AUTHORITATIVE_PLANNER_SYSTEM_PROMPT = ("):source.index("\n)\n", source.index("AUTHORITATIVE_PLANNER_SYSTEM_PROMPT = ("))]
    assert prompt.count("render_planner_example(") == 2 and '\\"id\\"' not in prompt and "'{\"" not in prompt   # no hand-written object left in the prompt


def test_the_example_follows_the_order_when_the_canonical_order_moves(monkeypatch):
    moved = dict(PLANNER_CANONICAL_KEY_ORDER)
    moved["GlobalInvariant"] = ("statement", "id")
    monkeypatch.setattr(plan_schema, "PLANNER_CANONICAL_KEY_ORDER", moved)
    assert list(planner_output_json_schema()["$defs"]["GlobalInvariant"]["properties"]) == ["statement", "id"]
    assert render_planner_example("GlobalInvariant", {"id": "gi1", "statement": "..."}) == '{"statement": "...", "id": "gi1"}'
    assert planning_request.render_planner_example is plan_schema.render_planner_example


# ---------------------------------------------------------------- fail closed: a shape that is not exactly the model's builds nothing

def _with(monkeypatch, table, name, value):
    if table == "PLANNER_CANONICAL_KEY_ORDER":
        monkeypatch.setattr(plan_schema, "PLANNER_CANONICAL_KEY_ORDER", {**PLANNER_CANONICAL_KEY_ORDER, name: value})
    else:
        assert table == "PLANNER_WIRE_REQUIRED"
        monkeypatch.setattr(plan_schema, "PLANNER_WIRE_REQUIRED", {**PLANNER_WIRE_REQUIRED, name: value})


@pytest.mark.parametrize("table,name,value", [
    ("PLANNER_CANONICAL_KEY_ORDER", "Subtask", PLANNER_CANONICAL_KEY_ORDER["Subtask"][:-1]),                 # a model key with no place in the order
    ("PLANNER_CANONICAL_KEY_ORDER", "Subtask", PLANNER_CANONICAL_KEY_ORDER["Subtask"] + ("invented",)),      # a key the model does not have
    ("PLANNER_CANONICAL_KEY_ORDER", "GlobalInvariant", ("id", "statement", "id")),                            # a key listed twice
    ("PLANNER_CANONICAL_KEY_ORDER", "NoSuchObject", ("id",)),
    ("PLANNER_CANONICAL_KEY_ORDER", "PlannerStructuredOutput",                                                # a required key behind an optional one
     ("acceptance_criteria", "global_invariants", "subtasks", "extension_points", "refactor_baseline", "integration_relationships")),
    ("PLANNER_WIRE_REQUIRED", "PlannerStructuredOutput", ("global_invariants", "subtasks", "invented")),
    ("PLANNER_WIRE_REQUIRED", "PlannerStructuredOutput", ("global_invariants", "subtasks", "refactor_baseline")),   # required, but not among the first keys
    ("PLANNER_WIRE_REQUIRED", "NoSuchObject", ("id",)),
])
def test_a_shape_that_disagrees_with_the_models_builds_no_schema_and_renders_nothing(monkeypatch, table, name, value):
    _with(monkeypatch, table, name, value)
    with pytest.raises(PlannerWireShapeError):
        planner_output_json_schema()
    with pytest.raises(PlannerWireShapeError):
        render_planner_example("GlobalInvariant", {"id": "gi1", "statement": "..."})


def test_an_object_missing_from_the_order_builds_no_schema(monkeypatch):
    monkeypatch.setattr(plan_schema, "PLANNER_CANONICAL_KEY_ORDER", {k: v for k, v in PLANNER_CANONICAL_KEY_ORDER.items() if k != "MutationTarget"})
    with pytest.raises(PlannerWireShapeError):
        planner_output_json_schema()


def test_an_example_with_a_key_its_object_does_not_have_is_refused():
    with pytest.raises(PlannerWireShapeError, match="no keys"):
        render_planner_example("GlobalInvariant", {"id": "gi1", "text": "..."})
    with pytest.raises(PlannerWireShapeError, match="no keys"):
        render_planner_example("PlannerStructuredOutput", {"subtasks": [{"id": "s1", "files": []}]})
    with pytest.raises(PlannerWireShapeError, match="no object named"):
        render_planner_example("ExecutionMethod", {})


# ---------------------------------------------------------------- on the wire: the order reaches the provider

def test_the_canonical_order_and_required_keys_reach_the_native_provider():
    server = _NativeServer()
    try:
        server.reply["message"]["content"] = '{"global_invariants": [], "subtasks": []}'

        async def plan():
            llm = LLMClient(_config(server.url))
            try:
                return await PlannerAgent("planner", llm).run("plan the goal", system_prompt_override="You are the structured Planner.", json_mode=True,
                                                             response_schema=planner_output_json_schema())
            finally:
                await llm.aclose()

        asyncio.run(plan())
        [(_path, body)] = server.requests
    finally:
        server.close()
    sent = body["format"]                                    # parsed from the request bytes: key order is the order on the wire
    assert list(sent["properties"]) == list(PLANNER_CANONICAL_KEY_ORDER["PlannerStructuredOutput"]) and sent["required"] == ["global_invariants", "subtasks"]
    for name in MODELS:
        if name != "PlannerStructuredOutput":
            assert tuple(sent["$defs"][name]["properties"]) == PLANNER_CANONICAL_KEY_ORDER[name], name
    assert sent["$defs"]["IntegrationRelationship"]["required"] == ["id", "kind", "producer_subtask_ids", "consumer_subtask_ids", "relationship_statement"]
    assert json.dumps(sent) == json.dumps(planner_output_json_schema())                # the exact schema, order included

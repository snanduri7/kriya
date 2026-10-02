"""CI-6 (Code Intelligence R1 slice 2, items 9-11): the calibrated
ambiguity threshold, schema-constrained localization output and the
ambiguity-only model call.

The model is asked at most once, only when deterministic localization is
not clearly separated, only through a runtime that constrains output to a
JSON schema; its answer is the whole response as one JSON object whose ids
must be shown, current candidates - never a substring heuristic.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_provider_contract_native import _config as native_config
from test_provider_contract_native import _NativeServer

from kriya.config import AppConfig
from kriya.core.completion import CompletionResult, CompletionStatus
from kriya.core.llm import STRUCTURED_OUTPUT_UNSUPPORTED, LLMClient, StructuredOutputUnsupportedError
from kriya.workflow import localization_decision as ld
from kriya.workflow.graph_retrieval import GraphRetrievalResult, LocalizationCandidate


def _cand(symbol_id, path="A.java", kind="method", score=20.0):
    return LocalizationCandidate(symbol_id, path, kind, symbol_id.split("#")[-1], f"void {symbol_id}()", score,
                                 (("fts", 10.0), ("vector", 10.0)))


CANDIDATES = [_cand("java:A.java#a.A.x()"), _cand("java:B.java#b.B.y()", "B.java"),
              _cand("properties:app.properties#y.limit", "app.properties", "config_key")]
IDS = [c.symbol_id for c in CANDIDATES]
AMBIGUOUS = {"exact": False, "margin": 2.0}


def test_the_calibrated_threshold():
    assert ld.is_clear({"exact": True, "margin": 10.0})
    assert not ld.is_clear({"exact": True, "margin": 9.9})  # exact but not separated
    assert not ld.is_clear({"exact": False, "margin": 60.0})  # separated similarity is still similarity
    assert not ld.is_clear(None)


def test_the_schema_enumerates_exactly_the_shown_candidates_and_nothing_else():
    schema = ld.decision_schema(IDS)
    assert schema["properties"]["target_symbol_ids"]["items"]["enum"] == IDS
    assert schema["properties"]["target_symbol_ids"]["minItems"] == 1
    assert schema["additionalProperties"] is False and set(schema["required"]) == {"target_symbol_ids", "reasons"}


@pytest.mark.parametrize("content, reason", [
    ('Sure! {"target_symbol_ids": ["java:A.java#a.A.x()"], "reasons": ["x"]}', ld.MALFORMED),  # no substring
    ('{"target_symbol_ids": ["java:A.java#a.A.x()"], "reasons": [], "note": "x"}', ld.MALFORMED),
    ('{"target_symbol_ids": [], "reasons": []}', ld.MALFORMED),
    ('{"target_symbol_ids": "java:A.java#a.A.x()", "reasons": []}', ld.MALFORMED),
    ('{"target_symbol_ids": ["java:Z.java#z.Z.q()"], "reasons": []}', ld.UNKNOWN_SYMBOL),  # never shown
    ('{"target_symbol_ids": ["java:B.java#b.B.y()"], "reasons": []}', ld.UNKNOWN_SYMBOL),  # shown, now stale
])
def test_a_decision_is_one_json_object_of_shown_current_ids(content, reason):
    with pytest.raises(ld.DecisionRejected) as rejected:
        ld.parse_decision(content, IDS, current=lambda i: i != "java:B.java#b.B.y()")
    assert rejected.value.reason_code == reason


def test_a_valid_decision_is_parsed_and_adopted_without_dropping_anything():
    decision = ld.parse_decision(json.dumps({"target_symbol_ids": ["java:B.java#b.B.y()"],
                                             "config_symbol_ids": ["properties:app.properties#y.limit"],
                                             "reasons": ["y applies the limit"]}), IDS, current=lambda i: True)
    assert decision.target_symbol_ids == ("java:B.java#b.B.y()",)
    result = GraphRetrievalResult(localization=list(CANDIDATES), matched_files=["A.java", "B.java"],
                                  file_scores={"A.java": 2.0, "B.java": 1.5},
                                  retrieval_member_hints={"B.java": ["B.other"]},
                                  current_member_ids={"java:B.java#b.B.y()": "B.y"})
    result.adopt_decision(decision.target_symbol_ids)
    assert [c.symbol_id for c in result.localization] == ["java:B.java#b.B.y()", IDS[0], IDS[2]]
    assert result.matched_files == ["B.java", "A.java"] and result.file_scores["B.java"] == 2.0
    assert result.retrieval_member_hints["B.java"] == ["B.y", "B.other"]


def _llm_returning(content):
    llm = AsyncMock()
    llm.complete_result = AsyncMock(return_value=CompletionResult(
        status=CompletionStatus.OK, model="m", content=content, prompt_tokens=321))
    return llm


def test_a_clear_or_single_candidate_never_calls_the_model():
    llm = _llm_returning("{}")
    assert asyncio.run(ld.decide(llm, "g", CANDIDATES, {"exact": True, "margin": 30.0},
                                 lambda i: True)).reason_code == ld.CLEAR
    assert asyncio.run(ld.decide(llm, "g", CANDIDATES[:1], AMBIGUOUS, lambda i: True)).reason_code == \
        ld.SINGLE_CANDIDATE
    assert asyncio.run(ld.decide(llm, "g", [], AMBIGUOUS, lambda i: True)).reason_code == ld.NO_CANDIDATES
    llm.complete_result.assert_not_awaited()


def test_an_ambiguous_localization_makes_exactly_one_schema_call_and_records_its_cost():
    llm = _llm_returning('{"target_symbol_ids": ["java:B.java#b.B.y()"], "reasons": ["named behaviour"]}')
    outcome = asyncio.run(ld.decide(llm, "make y respect the limit", CANDIDATES, AMBIGUOUS, lambda i: True))
    assert (outcome.reason_code, outcome.called, outcome.prompt_tokens) == (ld.DECIDED, True, 321)
    assert outcome.decision.target_symbol_ids == ("java:B.java#b.B.y()",)
    [call] = llm.complete_result.await_args_list
    assert call.kwargs["response_schema"]["properties"]["target_symbol_ids"]["items"]["enum"] == IDS
    assert "id=java:B.java#b.B.y()" in call.args[1] and "make y respect the limit" in call.args[1]


def test_a_runtime_without_schema_output_is_skipped_typed_before_any_request():
    config = AppConfig()  # the packaged /v1 adapter declares no json_schema_output
    config.llm_chain = []
    llm = LLMClient(config)
    llm._request_once = AsyncMock(side_effect=AssertionError("no provider contact"))  # pylint: disable=protected-access
    with pytest.raises(StructuredOutputUnsupportedError) as refused:
        asyncio.run(llm.complete_result("s", "u", response_schema={"type": "object"}))
    assert refused.value.reason_code == STRUCTURED_OUTPUT_UNSUPPORTED
    outcome = asyncio.run(ld.decide(llm, "g", CANDIDATES, AMBIGUOUS, lambda i: True))
    assert (outcome.reason_code, outcome.called) == (ld.SCHEMA_UNSUPPORTED, False)


def test_the_native_runtime_sends_the_schema_as_format_and_never_drops_it():
    server = _NativeServer()
    try:
        server.reply["message"]["content"] = '{"target_symbol_ids": ["java:A.java#a.A.x()"], "reasons": ["x"]}'
        config = native_config(server.url)
        config.llm.reasoning = True
        llm = LLMClient(config)
        try:
            outcome = asyncio.run(ld.decide(llm, "g", CANDIDATES, AMBIGUOUS, lambda i: True))
            [(path, body)] = server.requests
            assert path == "/api/chat" and body["format"] == ld.decision_schema(IDS)
            assert outcome.reason_code == ld.DECIDED
            # A refused schema request is not retried without its schema.
            server.requests.clear()
            server.status, server.error = 400, "invalid format"
            failed = asyncio.run(llm.complete_result("s", "u", response_schema=ld.decision_schema(IDS)))
            assert failed.status is not CompletionStatus.OK
            assert len(server.requests) == 1 and "format" in server.requests[0][1]
        finally:
            asyncio.run(llm.aclose())
    finally:
        server.close()

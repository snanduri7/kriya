"""TIME-BUDGET-EXHAUSTED-MODEL-ATTRIBUTION-001 (owner rulings 2026-11-13 after Scenario-A run 9): once the generation budget is exhausted the
attribution is deterministic and names no source; no attribution model request is built.

MEASURED in run 9 (records seq 665-672): the third s3 invocation stopped on `time_budget_exhausted` with 0.0 s remaining; the retry strategy
then built a source-attribution model request, which the exhausted deadline refused at dispatch (`dispatched: false`), and the diagnosis said
`attribution_kind: SOURCE_DEFECT`, tier `full_set`, confidence `low`. A budget stop is neither a source defect nor an infrastructure failure:
it is a resource/policy termination (owner ruling), so it has its own kind, RESOURCE_LIMIT.
Pre-fix behaviour, measured with this file's first test on b141fdd's attribution code: one model call, SOURCE_DEFECT
(~/kriya-evidence/product-72h-confidence/run9-fixes-2026-11-13/budget-attribution/).
"""
from unittest.mock import AsyncMock

import pytest
from test_workflow import GenerationState, _minimal_attempt_ctx, handle_attempt_failure

from kriya.workflow.failure import (
    INFRASTRUCTURE_STOP_FAILURE_TYPES,
    RESOURCE_LIMIT_FAILURE_TYPES,
    Failure,
    FailureAttributionKind,
    QualityGateFailure,
    classify_failure_attribution,
)

RUN9_MESSAGE = ("GENERATION TIME BUDGET EXHAUSTED: refusing to start a 2-file generation pass with 0.0s remaining; estimated generation plus "
                "gate reserve requires 300.0s.")
RUN9_OUTPUT = "remaining_seconds=0.0; estimated_generation_seconds=180.0; gate_reserve_seconds=120"
TRIAGE_ANSWER = '{"files": ["protocol.py"], "confidence": "medium", "reasoning": "the parser writes eight bytes"}'


def _two_file_attempt(tmp_path):
    """The run-9 shape: a two-file unit (the triage tier, the one that calls a model, is skipped for a single candidate file)."""
    (tmp_path / "protocol.py").write_text("X = 1\n")
    (tmp_path / "parser.py").write_text("def encode():\n    return b''\n")
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    state.all_files_written = {"protocol.py", "parser.py"}
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    ctx.developer.llm.complete = AsyncMock(return_value=TRIAGE_ANSWER)
    ctx.developer.run = AsyncMock(side_effect=AssertionError("the Developer must not be called"))
    return state, ctx


def _budget_stop() -> QualityGateFailure:
    return QualityGateFailure(Failure(type="time_budget_exhausted", message=RUN9_MESSAGE, raw_output=RUN9_OUTPUT))


@pytest.mark.asyncio
async def test_run9_shape_a_budget_stop_builds_no_attribution_request_and_blames_no_source(tmp_path):
    state, ctx = _two_file_attempt(tmp_path)

    should_break = await handle_attempt_failure(state, ctx, _budget_stop())

    assert should_break is True and state.environment_failure == RUN9_MESSAGE     # the stop itself is what it was
    assert ctx.developer.llm.complete.await_count == 0                            # no attribution model request is even built
    assert ctx.developer.run.await_count == 0
    assert state.last_failure.attribution_kind == "RESOURCE_LIMIT"                # not SOURCE_DEFECT, not INFRASTRUCTURE_DEFECT
    assert (state.last_attribution.tier, state.last_attribution.files, state.last_attribution.confidence) == ("full_set", [], "high")
    assert state.last_attribution.reasoning == (
        "RESOURCE_LIMIT is a resource/policy termination of the run; no source file owns it and source-file attribution is not performed.")
    assert state.last_failure.likely_files == [] and not state.last_implicated_files
    assert state.plan_scope_conflict is None                                      # no owner is reopened, no repair is scheduled


def test_the_resource_limit_types_are_a_closed_table_distinct_from_infrastructure():
    assert RESOURCE_LIMIT_FAILURE_TYPES == frozenset({"time_budget_exhausted"})
    assert not RESOURCE_LIMIT_FAILURE_TYPES & INFRASTRUCTURE_STOP_FAILURE_TYPES
    assert classify_failure_attribution("time_budget_exhausted", RUN9_MESSAGE) is FailureAttributionKind.RESOURCE_LIMIT
    assert classify_failure_attribution("containment_setup_failed", "x") is FailureAttributionKind.INFRASTRUCTURE_DEFECT
    # owner ruling 2026-11-13: internal_framework_error is neither; it keeps its label until it is characterized on its own
    assert classify_failure_attribution("internal_framework_error", "x") is FailureAttributionKind.SOURCE_DEFECT
    assert {kind.value for kind in FailureAttributionKind} == {
        "SOURCE_DEFECT", "PLAN_SCOPE_DEFECT", "VERIFICATION_CONTRACT_DEFECT", "TEST_DEFECT", "INFRASTRUCTURE_DEFECT", "RESOURCE_LIMIT"}


@pytest.mark.asyncio
async def test_an_infrastructure_stop_keeps_its_own_kind_and_wording(tmp_path):
    """Negative control: the two non-source kinds are not merged."""
    state, ctx = _two_file_attempt(tmp_path)

    await handle_attempt_failure(state, ctx, QualityGateFailure(
        Failure(type="containment_setup_failed", message="CONTAINMENT_SETUP_FAILED: stopped", raw_output="stopped")))

    assert state.last_failure.attribution_kind == "INFRASTRUCTURE_DEFECT"
    assert "INFRASTRUCTURE_DEFECT is owned by the verification/control plane" in state.last_attribution.reasoning
    assert ctx.developer.llm.complete.await_count == 0


@pytest.mark.asyncio
async def test_an_ambiguous_candidate_failure_still_gets_its_triage_call(tmp_path):
    """Negative control: only the budget stop lost its model call."""
    state, ctx = _two_file_attempt(tmp_path)

    should_break = await handle_attempt_failure(state, ctx, QualityGateFailure(
        Failure(type="run_verification", message="expected 9 header bytes, got 13", raw_output="expected 9 header bytes, got 13")))

    assert should_break is False and ctx.developer.llm.complete.await_count == 1
    assert state.last_failure.attribution_kind == "SOURCE_DEFECT" and state.last_attribution.tier == "triage"

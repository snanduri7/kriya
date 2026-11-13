"""ENV-FAILURE-TRIAGE-MODEL-CALL-001 (R8-B, owner ruling 2026-11-12 after Scenario-A run 8).

MEASURED in run 8 (records seq 204-209): after the typed stop `CONTAINMENT_SETUP_FAILED` the retry strategy still ran source attribution; its
triage tier sent the docker error and the unit's source excerpts to the model, and the diagnosis record said `attribution_kind: SOURCE_DEFECT`.
Pre-fix characterization (passes on ff23469): ~/kriya-evidence/product-72h-confidence/run8-gaps-2026-11-12/r8b/.

The rule now: a typed containment setup stop is INFRASTRUCTURE_DEFECT (the existing kind), which the retry strategy already routes to a
deterministic no-file attribution without any model call. Candidate failures keep their attribution, the triage tier included.
"""
from unittest.mock import AsyncMock

import pytest
from test_workflow import GenerationState, _minimal_attempt_ctx, handle_attempt_failure

from kriya.tools.containment import PROXY_START_TIMEOUT, BackendUnavailableError, ProxyStartError
from kriya.workflow.failure import (
    INFRASTRUCTURE_STOP_FAILURE_TYPES,
    Failure,
    FailureAttributionKind,
    QualityGateFailure,
    classify_failure_attribution,
)

RUN8_ERROR = ("Failed to start the registry proxy: Command '['/usr/local/bin/docker', 'run', '-d', '--name', "
              "'kriya-acq-proxy-ccd09b51c1b5']' timed out after 20 seconds")
TRIAGE_ANSWER = '{"files": ["protocol.py"], "confidence": "medium", "reasoning": "the parser writes eight bytes"}'


def _two_file_attempt(tmp_path):
    """The run-8 shape: more than one candidate file (the triage tier is skipped for a single one)."""
    (tmp_path / "pom.py").write_text("X = 1\n")
    (tmp_path / "protocol.py").write_text("def encode():\n    return b''\n")
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    state.all_files_written = {"pom.py", "protocol.py"}
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    ctx.developer.llm.complete = AsyncMock(return_value=TRIAGE_ANSWER)
    ctx.developer.run = AsyncMock(side_effect=AssertionError("the Developer must not be called"))
    return state, ctx


def _assert_infrastructure_stop(state, ctx, should_break):
    assert should_break is True                                              # the loop stops: nothing is retried
    assert ctx.developer.llm.complete.await_count == 0                       # no attribution-model call
    assert ctx.developer.run.await_count == 0                                # no Developer call
    assert state.last_failure.attribution_kind == "INFRASTRUCTURE_DEFECT"    # never SOURCE_DEFECT
    assert (state.last_attribution.tier, state.last_attribution.files, state.last_attribution.confidence) == ("full_set", [], "high")
    assert "INFRASTRUCTURE_DEFECT is owned by the verification/control plane" in state.last_attribution.reasoning
    assert state.last_failure.likely_files == [] and not state.last_implicated_files
    assert state.plan_scope_conflict is None                                 # no owner is reopened, no repair is scheduled


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    BackendUnavailableError(RUN8_ERROR),
    ProxyStartError(PROXY_START_TIMEOUT, "Failed to start the registry proxy 'kriya-acq-proxy-x': still not running at the launch deadline"),
], ids=["run8_untyped_backend_error", "typed_proxy_start_timeout"])
async def test_a_containment_setup_stop_makes_no_model_call_and_blames_no_source_file(tmp_path, error):
    state, ctx = _two_file_attempt(tmp_path)

    should_break = await handle_attempt_failure(state, ctx, error)

    assert state.environment_failure.startswith("CONTAINMENT_SETUP_FAILED:")
    assert state.last_failure.type == "containment_setup_failed"
    _assert_infrastructure_stop(state, ctx, should_break)


@pytest.mark.asyncio
async def test_a_containment_stop_raised_as_a_gate_failure_bypasses_source_attribution_too(tmp_path):
    state, ctx = _two_file_attempt(tmp_path)

    should_break = await handle_attempt_failure(
        state, ctx, QualityGateFailure(Failure(type="containment_setup_failed", message="CONTAINMENT_SETUP_FAILED: stopped", raw_output="stopped")))

    assert state.environment_failure is not None
    _assert_infrastructure_stop(state, ctx, should_break)


def test_the_infrastructure_stop_types_are_a_closed_table_of_the_measured_type_only():
    """Only what run 8 measured. internal_framework_error keeps the label the kernel goldens pin
    (INFRA-STOP-ATTRIBUTION-SOURCE-DEFECT-LABEL-001, open); candidate types are never in the table. A budget stop is NOT infrastructure either:
    since run 9 it is RESOURCE_LIMIT (TIME-BUDGET-EXHAUSTED-MODEL-ATTRIBUTION-001, tests/test_time_budget_exhausted_attribution_001.py)."""
    assert INFRASTRUCTURE_STOP_FAILURE_TYPES == frozenset({"containment_setup_failed"})
    assert classify_failure_attribution("containment_setup_failed", "x") is FailureAttributionKind.INFRASTRUCTURE_DEFECT
    assert classify_failure_attribution("time_budget_exhausted", "x") is FailureAttributionKind.RESOURCE_LIMIT
    for unchanged in ("internal_framework_error", "compile", "run_verification", "general_error",
                      "static_analysis_blocked", "goal_spec_compliance"):
        assert classify_failure_attribution(unchanged, "x") is FailureAttributionKind.SOURCE_DEFECT
    assert classify_failure_attribution("test", "x") is FailureAttributionKind.TEST_DEFECT


# ---------------------------------------------------------------------------------------------- negative controls: candidate failures keep theirs

@pytest.mark.asyncio
async def test_a_genuine_source_failure_with_a_locator_is_still_attributed_to_its_file(tmp_path):
    state, ctx = _two_file_attempt(tmp_path)
    failure = Failure(type="compile", message="protocol.py:2: SyntaxError: invalid syntax", raw_output="protocol.py:2: SyntaxError: invalid syntax",
                      likely_files=["protocol.py"])

    should_break = await handle_attempt_failure(state, ctx, QualityGateFailure(failure))

    assert should_break is False                                             # a candidate failure is retried
    assert state.environment_failure is None
    assert state.last_failure.attribution_kind == "SOURCE_DEFECT"
    assert state.last_attribution.files == ["protocol.py"] and state.last_attribution.tier != "triage"
    assert ctx.developer.llm.complete.await_count == 0                       # a locator needs no model


@pytest.mark.asyncio
async def test_a_genuinely_ambiguous_candidate_failure_still_gets_the_triage_tier(tmp_path):
    state, ctx = _two_file_attempt(tmp_path)
    failure = Failure(type="run_verification", message="expected 9 header bytes, got 13", raw_output="expected 9 header bytes, got 13")

    should_break = await handle_attempt_failure(state, ctx, QualityGateFailure(failure))

    assert should_break is False and state.environment_failure is None
    assert ctx.developer.llm.complete.await_count == 1                       # the triage tier, where it is authorized
    assert ctx.developer.llm.complete.await_args.args[0].startswith("You are triaging a build/test/runtime failure")
    assert state.last_failure.attribution_kind == "SOURCE_DEFECT"
    assert (state.last_attribution.tier, state.last_attribution.files) == ("triage", ["protocol.py"])

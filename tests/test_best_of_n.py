"""Tests for kriya/workflow/best_of_n.py - sequential, first-attempt-only
Best-of-N. Unit tests exercise reset_state_for_independent_candidate() and
run_attempt_with_best_of_n() in isolation; one integration-level test in
tests/test_workflow.py (see test_workflow_best_of_n_never_activates_without_a_real_sandbox)
covers the workflow.py dispatch condition.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kriya.workflow.best_of_n import (
    BestOfNFailureRecorded,
    reset_state_for_independent_candidate,
    run_attempt_with_best_of_n,
)
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.state import GenerationState, RecoveryPhaseAdvanced


def _make_state(**overrides):
    state = GenerationState()
    state.error_context = "some error"
    state.last_implicated_files = ["a.java"]
    state.last_missing_files = ["b.java"]
    state.last_error_source_context = {"a.java": "context"}
    state.all_files_written = {"a.java", "b.java"}
    state.all_original_contents = {"a.java": "orig"}
    state.files_written = ["a.java"]
    state.budgets.retry_count = 3
    state.budgets.last_failure_signature = ("compile", "sig")
    state.attempt_number = 5
    state.gate_outcomes = [{"type": "compile"}]
    state.model_hops = ["model-a"]
    state.budgets.targeted_retry_count = 2
    state.budgets.fallback_targeted_attempted = True
    state.toolchain_checked = True
    state.run_verification_confirmed = True
    state.budgets.best_of_n_candidates_tried = 1
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _ctx():
    """The attempt-context fields best-of-N reads: the retry policy's
    budgets (4 full-set, 3 targeted, no fallback) and the workspace."""
    return SimpleNamespace(workspace_path="/fake/workspace", max_retries=4, targeted_max_retries=3, chain=[])


def test_reset_clears_candidate_specific_fields():
    state = _make_state()
    reset_state_for_independent_candidate(state)

    assert state.error_context == ""
    assert state.last_implicated_files is None
    assert state.last_missing_files is None
    assert state.last_error_source_context == {}
    assert state.all_files_written == set()
    assert state.all_original_contents == {}
    assert state.files_written == []
    assert state.budgets.retry_count == 0
    assert state.budgets.last_failure_signature is None


def test_reset_leaves_trace_and_environment_fields_untouched():
    state = _make_state()
    reset_state_for_independent_candidate(state)

    assert state.attempt_number == 5
    assert state.gate_outcomes == [{"type": "compile"}]
    assert state.model_hops == ["model-a"]
    assert state.budgets.targeted_retry_count == 2
    assert state.budgets.fallback_targeted_attempted is True
    assert state.toolchain_checked is True
    assert state.run_verification_confirmed is True
    # Accumulates across the whole run - never reset by this function itself.
    assert state.budgets.best_of_n_candidates_tried == 1


@pytest.mark.asyncio
async def test_first_candidate_success_returns_immediately():
    state = GenerationState()
    ctx = object()

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(return_value=None)) as mock_run, \
         patch("kriya.workflow.worktree.create_git_worktree") as mock_worktree:
        await run_attempt_with_best_of_n(state, ctx, n=3)

    mock_run.assert_awaited_once()
    mock_worktree.assert_not_called()
    assert state.budgets.best_of_n_candidates_tried == 0


@pytest.mark.asyncio
async def test_second_candidate_succeeds_after_first_fails():
    state = GenerationState()
    ctx = _ctx()
    failure = QualityGateFailure(Failure(type="compile", message="broke"))

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=[failure, None])), \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=AsyncMock(return_value=False)), \
         patch("kriya.workflow.worktree.create_git_worktree") as mock_worktree:
        await run_attempt_with_best_of_n(state, ctx, n=3)

    mock_worktree.assert_called_once_with("/fake/workspace")
    assert state.budgets.best_of_n_candidates_tried == 1


@pytest.mark.asyncio
async def test_all_candidates_failing_propagates_the_last_failure():
    state = GenerationState()
    ctx = _ctx()
    first_failure = QualityGateFailure(Failure(type="compile", message="first"))
    last_failure = QualityGateFailure(Failure(type="compile", message="last"))

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=[first_failure, last_failure])), \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=AsyncMock(return_value=False)), \
         patch("kriya.workflow.worktree.create_git_worktree"):
        with pytest.raises(QualityGateFailure) as exc_info:
            await run_attempt_with_best_of_n(state, ctx, n=2)

    assert exc_info.value is last_failure
    assert state.budgets.best_of_n_candidates_tried == 1


@pytest.mark.asyncio
async def test_stops_immediately_when_handle_attempt_failure_says_stop():
    """An environment/toolchain failure (handle_attempt_failure returning True)
    must not be papered over by trying another independent candidate."""
    state = GenerationState()
    ctx = _ctx()
    failure = QualityGateFailure(Failure(type="compile", message="env broke"))

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=failure)), \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=AsyncMock(return_value=True)) as handle, \
         patch("kriya.workflow.worktree.create_git_worktree") as mock_worktree:
        with pytest.raises(BestOfNFailureRecorded) as exc_info:
            await run_attempt_with_best_of_n(state, ctx, n=3)

    # Recorded once, here; the loop gets the record and its stop decision.
    handle.assert_awaited_once()
    assert exc_info.value.failure is failure and exc_info.value.stop_loop is True
    mock_worktree.assert_not_called()
    assert state.budgets.best_of_n_candidates_tried == 0


@pytest.mark.asyncio
async def test_worktree_reset_failure_hands_the_recorded_failure_to_the_loop_unreset():
    state = GenerationState()
    state.error_context = "recorded failure evidence"
    state.budgets.retry_count = 1
    ctx = _ctx()
    candidate_failure = QualityGateFailure(Failure(type="compile", message="candidate broke"))

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=candidate_failure)), \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=AsyncMock(return_value=False)) as handle, \
         patch("kriya.workflow.worktree.create_git_worktree", side_effect=RuntimeError("worktree gone")):
        with pytest.raises(BestOfNFailureRecorded) as exc_info:
            await run_attempt_with_best_of_n(state, ctx, n=3)

    handle.assert_awaited_once()
    assert exc_info.value.failure is candidate_failure and exc_info.value.stop_loop is False
    # No candidate followed, so none is counted and the recorded state stays.
    assert state.budgets.best_of_n_candidates_tried == 0
    assert state.error_context == "recorded failure evidence"
    assert state.budgets.retry_count == 1


@pytest.mark.asyncio
async def test_active_contract_recovery_hands_the_failure_to_the_loop_without_a_new_candidate():
    state = GenerationState()
    ctx = _ctx()
    failure = QualityGateFailure(Failure(type="compile", message="removed a public signature"))

    async def enter_recovery(state_, ctx_, exc):
        state_.api_contract_recovery = object()
        return False

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=failure)) as mock_run, \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=enter_recovery), \
         patch("kriya.workflow.worktree.create_git_worktree") as mock_worktree:
        with pytest.raises(BestOfNFailureRecorded) as exc_info:
            await run_attempt_with_best_of_n(state, ctx, n=3)

    assert exc_info.value.stop_loop is False
    mock_run.assert_awaited_once()
    mock_worktree.assert_not_called()
    assert state.budgets.best_of_n_candidates_tried == 0


@pytest.mark.asyncio
async def test_spent_budgets_hand_the_failure_to_the_loop_without_a_new_candidate():
    state = GenerationState()
    ctx = _ctx()
    failure = QualityGateFailure(Failure(type="compile", message="broke"))

    async def spend(state_, ctx_, exc):
        state_.budgets.retry_count = ctx_.max_retries
        return False

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=failure)) as mock_run, \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=spend), \
         patch("kriya.workflow.worktree.create_git_worktree") as mock_worktree:
        with pytest.raises(BestOfNFailureRecorded):
            await run_attempt_with_best_of_n(state, ctx, n=3)

    mock_run.assert_awaited_once()
    mock_worktree.assert_not_called()


@pytest.mark.asyncio
async def test_a_recovery_transition_is_never_handled_as_a_candidate_failure():
    state = GenerationState()
    transition = RecoveryPhaseAdvanced("RESTORE_PUBLIC_CONTRACT", "REPAIR_BEHAVIOR")

    with patch("kriya.workflow.attempt.run_attempt", new=AsyncMock(side_effect=transition)), \
         patch("kriya.workflow.retry_strategy.handle_attempt_failure", new=AsyncMock(return_value=False)) as handle, \
         patch("kriya.workflow.worktree.create_git_worktree") as mock_worktree:
        with pytest.raises(RecoveryPhaseAdvanced) as exc_info:
            await run_attempt_with_best_of_n(state, _ctx(), n=3)

    assert exc_info.value is transition
    handle.assert_not_awaited()
    mock_worktree.assert_not_called()

"""LR-R1-P4: a failed verification-only attempt earns another attempt only when
something can change what the next verification sees
(retry_strategy._admit_verification_only_retry). Case 1 is the reproducer
(tests/test_lr_r1_p4_verification_only_retry_reproducer.py)."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import test_lr_r1_p4_verification_only_retry_reproducer as repro

from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.failure import Failure
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.retry_progress import VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
from kriya.workflow.retry_strategy import _admit_verification_only_retry, verification_inputs_digest
from kriya.workflow.state import GenerationState
from kriya.workflow.verification_coordinator import is_verification_only_unit

TEST_VERIFIER = [{"type": "tool", "tool_name": "test", "description": "run the tests"}]


def _ctx(tmp_path, *, mode=WriteScopeMode.DENY_ALL, verification=None):
    (tmp_path / "shop").mkdir(exist_ok=True)
    service = tmp_path / repro.SERVICE
    if not service.exists():
        service.write_text(repro.PRODUCED_SOURCE)
    return SimpleNamespace(worktree_path=str(tmp_path), established_files=[repro.SERVICE],
                           required_verification=TEST_VERIFIER if verification is None else verification,
                           write_scope_mode=mode)


def _failed_verification(ctx, *, attempt=1):
    """State right after a verification-only attempt failed: what
    _run_verification_only_attempt records."""
    state = GenerationState()
    state.attempt_number = attempt
    state.verification_only_inputs = verification_inputs_digest(state, ctx)
    state.verification_only_inputs_attempt = attempt
    return state


def _failure():
    return Failure(type="test", message="1 failed", raw_output="E   assert 5 == 10", source="tests", attempt=1)


def _admissions(state):
    return [e.details for e in state.run_events if e.kind == "retry.verification_admission"]


# -- the decision -------------------------------------------------------------------------------------


def test_unchanged_inputs_are_not_retryable_whatever_budget_remains(tmp_path):   # case 5
    ctx = _ctx(tmp_path)
    state = _failed_verification(ctx)
    state.budgets.retry_count = 0   # the whole full-set budget remains
    _admit_verification_only_retry(state, ctx, _failure())
    [admission] = _admissions(state)
    assert admission["retryable"] is False and admission["reason_code"] == VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
    assert state.no_progress_terminated is True and state.no_progress_reason == VERIFICATION_RETRY_NO_CHANGE_POSSIBLE


def test_changed_verification_inputs_are_retryable(tmp_path):   # case 3
    ctx = _ctx(tmp_path)
    state = _failed_verification(ctx)
    (tmp_path / repro.SERVICE).write_text(repro.PRODUCED_SOURCE + "\nPAGE_SIZE = 10\n")   # a real change
    _admit_verification_only_retry(state, ctx, _failure())
    [admission] = _admissions(state)
    assert admission["workspace_changed"] is True and admission["retryable"] is True
    assert state.no_progress_terminated is False and state.no_progress_reason is None


def test_a_unit_with_mutation_authority_is_retryable(tmp_path):   # case 2 (decision level)
    ctx = _ctx(tmp_path)
    state = _failed_verification(ctx)
    ctx.write_scope_mode = WriteScopeMode.ALLOWLIST   # its next attempt may call the Developer
    _admit_verification_only_retry(state, ctx, _failure())
    [admission] = _admissions(state)
    assert admission["mutation_possible"] is True and admission["retryable"] is True
    assert state.no_progress_terminated is False


def test_attempt_number_and_process_identity_are_not_changes(tmp_path):   # case 7
    ctx = _ctx(tmp_path)
    first = _failed_verification(ctx, attempt=1)
    resumed = _failed_verification(ctx, attempt=7)   # a new process / resumed invocation, same content
    resumed.run_events = []
    assert first.verification_only_inputs == resumed.verification_only_inputs
    _admit_verification_only_retry(resumed, ctx, _failure())
    assert _admissions(resumed)[0]["retryable"] is False


@pytest.mark.parametrize("route", ["environment", "plan_scope", "no_progress"])
def test_an_existing_typed_route_decides_first(tmp_path, route):   # case 4 + recovery input
    """An infrastructure/environment stop (Kriya models no transient,
    retryable verifier failure: every infrastructure class is a typed stop),
    a plan-scope conflict (the controller reopens the owner: a recovery that
    can change the workspace) and the no-progress terminal are untouched."""
    ctx = _ctx(tmp_path)
    state = _failed_verification(ctx)
    if route == "environment":
        state.environment_failure = "verification infrastructure unavailable"
    elif route == "plan_scope":
        state.plan_scope_conflict = {"reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "required_files": [repro.SERVICE]}
    else:
        state.no_progress_terminated, state.no_progress_reason = True, "RETRY_NO_PROGRESS_EXHAUSTED"
    _admit_verification_only_retry(state, ctx, _failure())
    assert _admissions(state) == []
    assert state.no_progress_reason in (None, "RETRY_NO_PROGRESS_EXHAUSTED")


def test_a_failure_that_was_not_a_verification_only_attempt_is_untouched(tmp_path):
    ctx = _ctx(tmp_path)
    state = _failed_verification(ctx, attempt=1)
    state.attempt_number = 2   # a later, ordinary attempt failed
    _admit_verification_only_retry(state, ctx, _failure())
    assert _admissions(state) == [] and state.no_progress_terminated is False
    fresh = GenerationState()
    fresh.attempt_number = 1
    _admit_verification_only_retry(fresh, ctx, _failure())
    assert _admissions(fresh) == []


def test_the_verification_only_predicate_is_the_run_attempt_branch():
    assert is_verification_only_unit(WriteScopeMode.DENY_ALL, TEST_VERIFIER) is True
    assert is_verification_only_unit(WriteScopeMode.ALLOWLIST, TEST_VERIFIER) is False
    # DENY_ALL with nothing directly executable takes the ordinary (Developer) path.
    assert is_verification_only_unit(WriteScopeMode.DENY_ALL, [
        {"type": "manual", "description": "a human checks it"}]) is False


# -- end to end (the reproducer's real enforce loop) ---------------------------------------------------


def test_case6_verification_only_success_is_unchanged(tmp_path):
    with patch.object(repro, "FAILING", repro.PASSING):
        workspace, events, result, model_calls, test_gate_runs = repro._enforce(tmp_path)
    assert {r.subtask_id: r.status.value for r in result.subtask_results} == {"s1": "completed", "s2": "completed"}
    # Identical to the pre-fix code (MEASURED at f33d0be): s2's verification, then the enforce terminal's
    # own test gate - not a retry.
    assert test_gate_runs == ["s1", "s2", "s2"]
    assert [e.attempt for e in events if e.kind == "attempt.failed"] == []
    assert not [e for e in events if e.kind in ("retry.verification_admission", "retry.no_progress_terminal")]
    assert (workspace / repro.SERVICE).read_text() == repro.PRODUCED_SOURCE   # committed at the enforce terminal


_ORIGINAL_PLAN = repro._plan


def _allowlist_plan():
    plan = _ORIGINAL_PLAN().model_dump()
    plan["subtasks"][1]["planned_files"] = [{"path": repro.SERVICE, "action": "modify"}]
    plan["subtasks"][1].pop("execution_role", None)
    return EngineeringPlan.model_validate(plan)


def test_case2_a_unit_that_can_repair_keeps_its_retries(tmp_path):
    """The same failing tests, but s2 may write shop/service.py: the
    Developer can change the inputs, so P4 never suppresses its retries."""
    with patch.object(repro, "_plan", _allowlist_plan):
        _workspace, events, result, model_calls, test_gate_runs = repro._enforce(tmp_path)
    assert {r.subtask_id: r.status.value for r in result.subtask_results}["s2"] == "failed"
    assert test_gate_runs.count("s2") > 1                        # retried
    assert [c for c in model_calls if c[0] == "s2" and "Developer Agent" in c[1]]   # repaired by the Developer
    assert not [e for e in events if e.kind == "retry.verification_admission"]
    assert all(e.details["reason_code"] != VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
               for e in events if e.kind == "retry.no_progress_terminal")


def test_the_reproducer_still_stops_typed_when_the_run_resumes_from_its_inputs(tmp_path):
    """Case 7 end to end: the digest a resumed verification records equals the
    failed one (content only), so its failure is not retryable either."""
    from kriya.workflow import attempt as attempt_module

    recorded = []
    real = attempt_module._run_verification_only_attempt

    async def spy(state, ctx):
        try:
            await real(state, ctx)
        finally:
            recorded.append((state.attempt_number, state.verification_only_inputs))
    with patch.object(attempt_module, "_run_verification_only_attempt", spy):
        _workspace, events, _result, _calls, test_gate_runs = repro._enforce(tmp_path)
    # BACKEND-FINAL-CLOSURE-005: the controller reopens s1 once with s2's evidence (its gates run: the third
    # test-gate run); the identical regeneration is rejected, so s2 is verified exactly once (one recorded attempt).
    assert test_gate_runs == ["s1", "s2", "s1"] and len(recorded) == 1
    [(attempt, digest)] = recorded
    assert attempt == 1 and digest and len(digest) == 64
    [admission] = [e.details for e in events if e.kind == "retry.verification_admission"]
    assert admission["inputs_digest_at_verification"] == digest

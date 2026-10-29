"""B4 (Wave-2): a policy denial is terminal for the authority decision that produced it.

MEASURED at 1278bc0 (WAVE2_B1_B4/B4/PREFIX_probe_output): only FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE and TRUSTED_CONTROL_PATH_DENIED
stopped the retry loop; a real Developer write to a sensitive path (SENSITIVE_PATH_DENIED) became ``general_error``: the Developer
was re-prompted with "Policy denied ..." and a retry was charged, so the same denied action could be proposed again until the
budget ran out.

Invariant: every PolicyDeniedError that reaches the attempt loop un-grounded stops it at once with a typed reason; the denied
action executed nothing; no retry budget is charged and no fallback model is requested for it; a later attempt (a fresh run, so
fresh state and authority) is not blocked by it. Census of the producers (kriya/policy/filesystem.py writes and reads,
candidate_transaction scope, attempt.py control targets, policy/enforcement.py hard invariants, mcp/mcp.py invocation denial,
workflow.py authorize_action): each is a real policy refusal; none uses PolicyDeniedError for an infrastructure condition.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import developer_double
from test_prv17_preflight import _minimal_ctx

import kriya.workflow.recovery_coordinator as rc
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import WriteScopeMode
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.workflow.attempt import run_attempt
from kriya.workflow.retry_policy import charge_failed_attempt
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState


def _denial(reason_code, decision=PolicyDecision.DENY):
    return PolicyDeniedError(
        request=ActionRequest(action_type=ActionType.WRITE_FILE, target="x.py"),
        result=PolicyResult(decision=decision, reason_code=reason_code, explanation="refused"))


def _classify(exc, grounding=lambda exc, ctx: None):
    return rc.classify_attempt_exception(exc, object(), last_attempt_mode=None, ground_scope_denial=grounding)


async def _attempt(tmp_path, path):
    developer = developer_double()
    developer.run_generation = AsyncMock(return_value=[{"filepath": path, "content": "x = 1\n"}])
    ctx = _minimal_ctx(tmp_path, developer=developer, architect_files=[path], expected_files_upfront=[path],
                       architect_basename_to_path={Path(path).name: path}, allowed_write_relpaths=[path],
                       write_scope_mode=WriteScopeMode.ALLOWLIST)
    state = GenerationState()
    state.attempt_number, state.all_files_written = 0, set()
    return state, ctx, developer


# ---------------------------------------------------------------- the real mechanism: a Developer write refused by the writer

@pytest.mark.parametrize("path", [".env", "id_rsa", "secrets/.env"])
def test_a_real_sensitive_path_write_denial_stops_the_loop_without_a_charge_or_a_second_request(tmp_path, path):
    async def scenario():
        state, ctx, developer = await _attempt(tmp_path, path)
        with pytest.raises(PolicyDeniedError) as denied:
            await run_attempt(state, ctx)
        assert denied.value.result.reason_code == "SENSITIVE_PATH_DENIED"
        assert await handle_attempt_failure(state, ctx, denied.value) is True
        return state, developer

    state, developer = asyncio.run(scenario())
    assert not (tmp_path / path).exists()                                         # the denied action executed nothing
    assert state.unrecoverable_scope_denial_count == 1
    assert state.environment_failure.startswith("UNAUTHORIZED_GENERATION_TARGET: policy refused")
    assert "SENSITIVE_PATH_DENIED" in state.environment_failure                    # typed: the reason code travels with the stop
    assert (state.budgets.retry_count, state.budgets.targeted_retry_count) == (0, 0)   # not charged as a model failure
    assert not state.budgets.fallback_targeted_requested                          # no fallback model re-tries the denied action
    assert developer.run_generation.await_count == 1                              # and the Developer is never asked again


def test_the_historic_control_path_denial_keeps_its_own_message_and_stops(tmp_path):
    async def scenario():
        state, ctx, developer = await _attempt(tmp_path, ".git/config")
        with pytest.raises(PolicyDeniedError):
            await run_attempt(state, ctx)
        assert await handle_attempt_failure(state, ctx, PolicyDeniedError(
            request=ActionRequest(action_type=ActionType.WRITE_FILE, target=str(tmp_path / ".git/config")),
            result=PolicyResult(decision=PolicyDecision.DENY, reason_code="TRUSTED_CONTROL_PATH_DENIED", explanation="e"))) is True
        return state, developer

    state, developer = asyncio.run(scenario())
    assert state.environment_failure.startswith("UNAUTHORIZED_GENERATION_TARGET: the Developer proposed a write to repository metadata")
    assert developer.run_generation.await_count == 0                              # refused before any Developer request


def test_an_ordinary_failure_still_retries_and_is_charged(tmp_path):
    """The control: the terminal rule is about policy refusals, not about every exception."""
    async def scenario():
        state, ctx, _developer = await _attempt(tmp_path, "pkg/mod.py")
        stop = await handle_attempt_failure(state, ctx, ValueError("model produced garbage"))
        return state, stop

    state, stop = asyncio.run(scenario())
    assert stop is False and state.unrecoverable_scope_denial_count == 0 and state.environment_failure is None
    assert state.budgets.retry_count == 1


# ---------------------------------------------------------------- classification: every un-grounded denial, whatever its code

@pytest.mark.parametrize("code, decision", [
    ("FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE", PolicyDecision.DENY), ("TRUSTED_CONTROL_PATH_DENIED", PolicyDecision.DENY),
    ("SENSITIVE_PATH_DENIED", PolicyDecision.DENY), ("SENSITIVE_PATH_REQUIRES_APPROVAL", PolicyDecision.REQUIRE_APPROVAL),
    ("COMMAND_SUDO_DENIED", PolicyDecision.DENY), ("SOMETHING_NEW_DENIED", PolicyDecision.DENY),
])
def test_every_ungrounded_policy_denial_is_unrecoverable_with_its_reason_code(code, decision):
    classified = _classify(_denial(code, decision))
    assert classified.unrecoverable_scope_denial and classified.unrecoverable_denial_reason == code


def test_a_grounded_scope_denial_still_becomes_its_plan_scope_conflict():
    grounded = rc.Failure(type="plan_scope_conflict", message="owner", raw_output="owner")
    classified = _classify(_denial("FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"), grounding=lambda exc, ctx: grounded)
    assert classified.failure is grounded and not classified.unrecoverable_scope_denial
    assert classified.unrecoverable_denial_reason is None


@pytest.mark.parametrize("exc", [RuntimeError("x"), ValueError("x"), OSError("disk")])
def test_a_non_policy_exception_is_never_classified_as_a_policy_denial(exc):
    classified = _classify(exc)
    assert not classified.unrecoverable_scope_denial and classified.unrecoverable_denial_reason is None


# ---------------------------------------------------------------- the charge and the later attempt

def test_a_terminal_policy_denial_charges_no_budget_and_any_other_failure_still_does():
    class Budgets:
        retry_count = targeted_retry_count = api_contract_recovery_count = 0

    refused, ordinary = Budgets(), Budgets()
    charge_failed_attempt(refused, attempt_mode="full_set", plan_scope_conflict=False, failure_family_changed=False,
                          terminal_policy_denial=True)
    charge_failed_attempt(ordinary, attempt_mode="full_set", plan_scope_conflict=False, failure_family_changed=False)
    assert refused.retry_count == 0 and ordinary.retry_count == 1


def test_the_stop_belongs_to_the_run_state_so_a_fresh_attempt_is_not_blocked(tmp_path):
    """"A later attempt only after fresh authority/state": the refusal is recorded on the run's state, never on the workspace or a
    global; a new run (new state) is clean, and an ordinary failure on it is retried as usual."""
    async def scenario():
        state, ctx, _developer = await _attempt(tmp_path, ".env")
        with pytest.raises(PolicyDeniedError) as denied:
            await run_attempt(state, ctx)
        assert await handle_attempt_failure(state, ctx, denied.value) is True
        (tmp_path / "second").mkdir()
        fresh, fresh_ctx, _ = await _attempt(tmp_path / "second", "customers/views.py")
        retried = await handle_attempt_failure(fresh, fresh_ctx, ValueError("model produced garbage")) is False
        return state, fresh, retried

    state, fresh, retried = asyncio.run(scenario())
    assert state.unrecoverable_scope_denial_count == 1 and state.environment_failure
    assert fresh.unrecoverable_scope_denial_count == 0 and fresh.environment_failure is None and retried

"""Demo V2 owner decision (2026-10-15, option 1): MANAGED_SERVICE_APPROVAL_UNSUPPORTED.

Measured/traced (handover/DEMO-V2/GREENFIELD-OWNER-UX-FINDINGS.md section 10-A): a judgment with
``execution_mode == "managed_service"`` reached ``_execute_managed_service_verification`` from BOTH call-site families
(the verification-only ``_execute_runtime_verification_directly`` and ``run_attempt``'s inline block) before any approval
question existed, in human-in-the-loop mode. The model chose the service command and nothing asked a human.

Required invariant: in human-in-the-loop mode a managed-service verification has no reviewable subject, so it fails CLOSED
with a typed ``verification_infrastructure_failure`` (reason code MANAGED_SERVICE_APPROVAL_UNSUPPORTED) before the contract is
admitted, before any interpreter/build preparation and before any process is spawned. It is not a rejection (nobody refused
anything) and no approval is requested. Every other mode keeps today's behaviour.
"""
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_workflow import (
    _managed_service_judgment,
    _managed_service_plan,
    _managed_service_probe_result,
    _minimal_attempt_ctx,
    _runtime_verifier_ctx,
)

from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.attempt import _validate_and_convert_managed_service_contract as _ADMISSION
from kriya.workflow.attempt import run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

CODE = "MANAGED_SERVICE_APPROVAL_UNSUPPORTED"
HITL = "human-in-the-loop"
NON_HITL = "autonomous"
FINITE = {
    "should_run": True, "run_commands": [["java", "-cp", "target/classes", "com.example.App"]],
    "command_source": "inferred", "success_criteria": "prints HELLO",
}


def _verification_only_ctx(tmp_path, *, mode, judgment):
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("the Developer must not be called for a verification-only unit"))
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value=judgment)
    run_verifier.grade = AsyncMock(return_value={"passed": True, "reasoning": "ok", "likely_files": []})
    ctx = _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier)
    ctx.kernel.config.autonomy.mode = mode
    ctx.approval_callback = AsyncMock(return_value=True)
    return ctx


def _mutating_ctx(tmp_path, *, mode, judgment=None):
    plan = _managed_service_plan()
    views_py = "customers/views.py"
    developer = developer_double()
    developer.run_generation = AsyncMock(return_value=[{"filepath": views_py, "content": "# views\n"}])
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value=judgment or _managed_service_judgment())
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, run_verifier=run_verifier, goal="Create a Python 3.12 Django application.",
        architect_files=[views_py], expected_files_upfront=[views_py], architect_basename_to_path={"views.py": views_py},
        allowed_write_relpaths=[views_py], write_scope_mode=WriteScopeMode.ALLOWLIST,
        structured_plan=plan, current_subtask_id="s4", runtime_verification_required=True,
        required_verification=[{
            "type": "judgment", "description": "GET /customers/health returns 200",
            "tool_name": None, "verifier_kind": "application_runtime", "requires_runtime_execution": True,
        }],
    )
    ctx.kernel.config.autonomy.mode = mode
    ctx.approval_callback = AsyncMock(return_value=True)
    return ctx


def _state():
    state = GenerationState()
    state.attempt_number = 0
    state.all_files_written = {"manage.py"}
    return state


async def _run(ctx, state):
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}), \
            patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
            patch("kriya.workflow.attempt.run_managed_service_verification", return_value=_managed_service_probe_result()) as executor, \
            patch("kriya.workflow.attempt._validate_and_convert_managed_service_contract",
                  wraps=_ADMISSION) as admission, \
            patch("kriya.tools.process.ProcessController.start_managed", side_effect=AssertionError("a process was spawned")) as spawn:
        try:
            await run_attempt(state, ctx)
            raised = None
        except QualityGateFailure as failure:
            raised = failure
    return raised, executor, admission, spawn


async def _assert_failed_closed(raised, executor, admission, spawn, ctx, state):
    assert raised is not None, "the managed service ran / the attempt succeeded in human-in-the-loop mode"
    failure = raised.failure
    assert failure.type == "verification_infrastructure_failure"
    assert failure.diagnostics["reason_code"] == CODE
    assert CODE in failure.message and "No service was started" in failure.message
    assert state.gate_outcomes[-1]["type"] == "verification_infrastructure_failure"
    executor.assert_not_called()
    admission.assert_not_called()  # before the contract is admitted, hence before interpreter/build preparation
    spawn.assert_not_called()
    ctx.approval_callback.assert_not_called()  # nothing is asked with files=[]: there is no supported approval subject
    assert state.run_verification_declined is False  # not a human refusal
    assert state.run_verification_approved_fingerprint is None
    # The existing STOP_ENVIRONMENT classification: the run stops without a Developer retry.
    state.last_attempt_mode = state.last_attempt_mode or "full_set"
    assert await handle_attempt_failure(state, ctx, raised) is True
    assert state.environment_failure and CODE in state.environment_failure


# ------------------------------------------------------------------ A. HITL + managed_service, both call-site families
@pytest.mark.asyncio
async def test_a1_hitl_managed_service_verification_only_unit_fails_closed(tmp_path):
    ctx = _verification_only_ctx(tmp_path, mode=HITL, judgment=_managed_service_judgment())
    state = _state()
    raised, executor, admission, spawn = await _run(ctx, state)
    await _assert_failed_closed(raised, executor, admission, spawn, ctx, state)
    ctx.developer.run_generation.assert_not_called()


@pytest.mark.asyncio
async def test_a2_hitl_managed_service_inline_block_fails_closed(tmp_path):
    ctx = _mutating_ctx(tmp_path, mode=HITL)
    state = _state()
    raised, executor, admission, spawn = await _run(ctx, state)
    await _assert_failed_closed(raised, executor, admission, spawn, ctx, state)


@pytest.mark.asyncio
async def test_a3_even_a_user_authored_looking_label_does_not_open_the_gate(tmp_path):
    """command_source is the model's own label and never authority."""
    ctx = _verification_only_ctx(tmp_path, mode=HITL, judgment=_managed_service_judgment(command_source="goal_explicit"))
    state = _state()
    raised, executor, admission, spawn = await _run(ctx, state)
    await _assert_failed_closed(raised, executor, admission, spawn, ctx, state)


# ------------------------------------------------------------------ B. HITL + finite runtime command keeps its approval path
@pytest.mark.asyncio
async def test_b_hitl_finite_command_still_reaches_the_runtime_command_approval(tmp_path):
    ctx = _verification_only_ctx(tmp_path, mode=HITL, judgment=dict(FINITE))
    state = _state()
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence",
               return_value={"success": True, "timed_out": False, "returncode": 0, "output": "HELLO", "steps": []}) as run_app:
        with patch("kriya.workflow.attempt.run_managed_service_verification") as executor:
            await run_attempt(state, ctx)
    ctx.approval_callback.assert_called_once()
    run_app.assert_called_once()
    executor.assert_not_called()


# ------------------------------------------------------------------ C. non-HITL managed_service is unchanged
@pytest.mark.asyncio
@pytest.mark.parametrize("builder", ["verification_only", "inline"])
async def test_c_non_hitl_managed_service_still_executes(tmp_path, builder):
    if builder == "verification_only":
        ctx = _verification_only_ctx(tmp_path, mode=NON_HITL, judgment=_managed_service_judgment())
    else:
        ctx = _mutating_ctx(tmp_path, mode=NON_HITL)
    state = _state()
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}), \
            patch("kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""}), \
            patch("kriya.workflow.attempt.run_managed_service_verification", return_value=_managed_service_probe_result()) as executor:
        await run_attempt(state, ctx)
    executor.assert_called_once()
    ctx.approval_callback.assert_not_called()


# ------------------------------------------------------------------ D. no runtime requirement: nothing is invented
@pytest.mark.asyncio
async def test_d_hitl_judge_asks_for_no_run_so_no_managed_service_and_no_failure(tmp_path):
    judgment = _managed_service_judgment(should_run=False)
    ctx = _verification_only_ctx(tmp_path, mode=HITL, judgment=judgment)
    state = _state()
    raised, executor, admission, spawn = await _run(ctx, state)
    assert raised is None
    executor.assert_not_called()
    admission.assert_not_called()
    spawn.assert_not_called()
    ctx.approval_callback.assert_not_called()


# ------------------------------------------------------------------ the guard is keyed on the mode alone
def test_the_unsupported_failure_is_not_a_human_rejection_taxonomy():
    from kriya.workflow.failure import FailureAttributionKind, classify_failure_attribution

    kind = classify_failure_attribution("verification_infrastructure_failure", f"{CODE}: no approval subject")
    assert kind is FailureAttributionKind.INFRASTRUCTURE_DEFECT

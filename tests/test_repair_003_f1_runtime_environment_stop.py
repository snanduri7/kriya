"""RUNTIME-VERIFICATION-ENV-STOP-001 (P2, third repair cycle review finding F1, owner decision 2026-10-10: FIX NOW):
the deterministic reproducer written BEFORE the production change (ENGINEERING_RULES rule 4).

Measured (review of main..6d97558, repair-003/scratch/f1_runtime_probe_output.txt): a project that REQUIRES its own
verification environment (a real dependency declaration) whose ``python -m venv`` fails makes ``run_app`` /
``run_app_sequence`` return the typed result VERIFICATION-UNIT-ENV-FALLBACK-001 (c24bcce) introduced -
``environment_reason_code == PYTHON_ENVIRONMENT_UNAVAILABLE``, nothing launched - but the runtime-verification consumer
(``attempt._raise_runtime_verification_infrastructure_failure`` -> ``acceptance.runtime_verification_infrastructure_reason``)
reads output text and steps only, never the structured code, and returns None. The result is then graded as the
candidate's runtime behaviour (gate type ``run_verification``) and routed to Developer retry - the same
false-negative / wasted-retry class 13.5 fixed on the test gate, on the runtime gate. The managed-service admission
(``_validate_and_convert_managed_service_contract``) already refuses to start, but as MANAGED_SERVICE_CONTRACT_INVALID
without the reason code.

Required invariant (owner, 2026-10-10): when interpreter/venv preparation failed and the structured environment reason
code is present, runtime verification stops as an ENVIRONMENT failure (the existing ``verification_infrastructure_failure``
/ STOP_ENVIRONMENT path, ``diagnostics.reason_code`` carrying the code) BEFORE grading candidate runtime behaviour,
before Developer retry and before owner recovery - keyed on structured fields only, never output text. The
deterministic-sequence early return stays for commands that actually executed with process authority. No
false-success semantics change: failure typing and retry suppression only.
"""
import sys
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_workflow import _runtime_verifier_ctx

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.attempt import _raise_runtime_verification_infrastructure_failure, run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.retry_progress import VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

PYTHON_ENVIRONMENT_UNAVAILABLE = "PYTHON_ENVIRONMENT_UNAVAILABLE"
INFRA = "verification_infrastructure_failure"
ENSUREPIP_FAILURE = ("Error: Command '['.kriya/venv/bin/python3', '-m', 'ensurepip', '--upgrade', '--default-pip']' "
                     "returned non-zero exit status 1.")
APP_OK = "print('APP_RAN')\n"
APP_CRASH = "import sys\nprint('APP_RAN')\nsys.exit(3)\n"
TESTS_FAIL = "def test_x():\n    assert 1 == 2\n"
RUN_APP = [["python", "app.py"]]
RUN_PYTEST = [["python", "-m", "pytest", "-q", "tests"]]


def _workspace(tmp_path, *, app=APP_OK, manifest=True, tests=None):
    (tmp_path / "app.py").write_text(app)
    if manifest:
        (tmp_path / "requirements.txt").write_text("packaging>=20\n")  # a real declaration: the project requires its own environment
    if tests is not None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(tests)
    return tmp_path


class _VenvCreationFails:
    """The one stub (shared shape with test_repair_003_p2_2_venv_environment_failure): ``python -m venv`` exits 1,
    nothing is created; every other command runs for real. ``commands`` records every argv launched."""

    def __init__(self):
        self.commands = []
        real = PolymorphicValidator._run_cmd_with_timeout

        def method(validator, cmd, cwd, **kwargs):
            self.commands.append(list(cmd))
            if len(cmd) >= 3 and cmd[1:3] == ["-m", "venv"]:
                return {"returncode": 1, "stdout": "", "stderr": ENSUREPIP_FAILURE, "timeout": False}
            return real(validator, cmd, cwd, **kwargs)
        self.method = method


def _app_launched(commands):
    """Runtime steps the validator really launched (run_app_sequence -> _run_runtime_step -> _run_cmd_with_timeout)."""
    return [c for c in commands if "app.py" in c or "pytest" in c]


def _validator(root):
    return PolymorphicValidator(str(root), original_workspace_path=str(root), autonomy_cfg=AutonomyConfig())


def _verification_only_ctx(tmp_path, run_commands, *, managed_service=None):
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: the Developer is never invoked"))
    run_verifier = AsyncMock()
    judgment = {"should_run": True, "run_commands": run_commands, "command_source": "inferred",
                "success_criteria": "prints APP_RAN"}
    if managed_service is not None:
        judgment["execution_mode"] = "managed_service"
        judgment["managed_service"] = managed_service
    run_verifier.judge = AsyncMock(return_value=judgment)
    run_verifier.grade = AsyncMock(return_value={"passed": False, "reasoning": "graded the candidate", "likely_files": []})
    ctx = _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier,
                                established_files=["app.py"], max_retries=4)
    return ctx, developer, run_verifier


async def _assert_stopped_before_retry(state, ctx, raised):
    state.last_attempt_mode = state.last_attempt_mode or "full_set"  # as the P2-2 reproducer: the LR-R1-P4 admission input
    should_break = await handle_attempt_failure(state, ctx, raised)
    assert should_break is True
    assert state.environment_failure and state.environment_failure.startswith("VERIFICATION_INFRASTRUCTURE_FAILURE:")
    assert state.no_progress_terminated is False and state.no_progress_reason is None  # never the owner-recovery trigger
    assert state.budgets.last_failure_signature[0] == INFRA  # never the run_verification family


# ---------------------------------------------------------------- the measured shape through the real runtime-verification path
@pytest.mark.asyncio
async def test_f1_a_failed_required_venv_stops_runtime_verification_typed_before_grading_and_retry(tmp_path):
    """run_attempt -> verification-only path -> VerificationCoordinator -> _execute_runtime_verification_directly ->
    the REAL run_app_sequence (only ``python -m venv`` stubbed) -> the runtime consumer. The gate must raise the
    typed environment stop: the grader is never asked about behaviour that was never observed, and the retry strategy
    takes STOP_ENVIRONMENT."""
    _workspace(tmp_path)
    ctx, developer, run_verifier = _verification_only_ctx(tmp_path, RUN_APP)
    state = GenerationState()
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type == INFRA, (failure.type, failure.message[:200])
    assert failure.diagnostics["reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert "VERIFICATION_INFRASTRUCTURE_FAILURE" in failure.message and "ensurepip" in failure.message
    run_verifier.grade.assert_not_awaited()  # nothing about the candidate was observed, so nothing is graded
    assert not developer.run_generation.called
    assert _app_launched(stub.commands) == [], stub.commands
    assert [o["type"] for o in state.gate_outcomes][-1:] == [INFRA]
    await _assert_stopped_before_retry(state, ctx, raised.value)


@pytest.mark.parametrize("commands", [RUN_APP, RUN_PYTEST], ids=["application", "deterministic_test_sequence"])
def test_f1_the_shared_consumer_stops_on_the_structured_code_for_every_caller(tmp_path, commands):
    """The one consumer both the verification-only path and the mutating path's inline runtime block call, fed the
    REAL producer result. A deterministic sequence that never executed (the environment was never prepared) must
    not take the early return reserved for commands that ran with process authority."""
    _workspace(tmp_path, tests=TESTS_FAIL)
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        run_res = _validator(tmp_path).run_app_sequence(commands)
    assert run_res["environment_reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE and _app_launched(stub.commands) == []
    state = GenerationState()
    state.attempt_number = 1
    with pytest.raises(QualityGateFailure) as raised:
        _raise_runtime_verification_infrastructure_failure(state, run_res, commands)
    failure = raised.value.failure
    assert failure.type == INFRA and failure.diagnostics["reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert [o["type"] for o in state.gate_outcomes] == [INFRA]


@pytest.mark.asyncio
async def test_f1_managed_service_admission_carries_the_environment_code_not_a_contract_verdict(tmp_path):
    """The managed-service admission grounds the service command through the same interpreter resolver. A required
    environment that cannot be created is an ENVIRONMENT stop with the reason code - not an invalid contract (a
    judgment-shaped reason) - and the service never starts."""
    _workspace(tmp_path)
    service = {"service_command": ["python", "app.py"],
               "readiness": {"kind": "http", "host": "127.0.0.1", "port": 8765, "path": "/"},
               "probe": {"method": "GET", "host": "127.0.0.1", "port": 8765, "path": "/", "expected_status": 200}}
    ctx, developer, run_verifier = _verification_only_ctx(tmp_path, [], managed_service=service)
    state = GenerationState()
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method), \
            patch("kriya.workflow.attempt.run_managed_service_verification",
                  side_effect=AssertionError("the service must never start without its environment")):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type == INFRA
    assert failure.diagnostics and failure.diagnostics["reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE, failure.message[:200]
    assert "MANAGED_SERVICE_CONTRACT_INVALID" not in failure.message
    run_verifier.grade.assert_not_awaited()
    assert not developer.run_generation.called
    await _assert_stopped_before_retry(state, ctx, raised.value)


# ---------------------------------------------------------------- negative controls: behaviour that must not change
@pytest.mark.asyncio
async def test_f1_control_an_application_that_ran_and_failed_is_still_graded_and_retried(tmp_path):
    """No environment code: the application really launched (exit 3). The runtime gate grades its behaviour and the
    failure stays in the run_verification family, routed exactly as before (no environment stop)."""
    _workspace(tmp_path, app=APP_CRASH, manifest=False)  # dependency-free: Kriya's own interpreter, no venv needed
    ctx, _developer, run_verifier = _verification_only_ctx(tmp_path, RUN_APP)
    state = GenerationState()
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type != INFRA and "APP_RAN" in failure.raw_output
    assert not (failure.diagnostics or {}).get("reason_code")
    run_verifier.grade.assert_awaited_once()
    assert len(_app_launched(stub.commands)) == 1
    # A verification-only unit cannot change its inputs, so its genuine runtime failure ends on the PRD-026
    # no-progress terminal (LR-R1-P4 admission, retry_strategy) - the owner-recovery route, NOT the environment stop.
    # (Pre-fix measurement, repair-003/prefix/F1_reproducer_before.txt: this control first asserted a Developer
    # retry, which the admission never grants a verification-only unit; the expectation was corrected, not the code.)
    assert await handle_attempt_failure(state, ctx, raised.value) is True
    assert state.environment_failure is None
    assert state.no_progress_terminated is True and state.no_progress_reason == VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
    assert state.budgets.last_failure_signature[0] == failure.type  # the run_verification family, never INFRA


def test_f1_control_a_deterministic_sequence_that_executed_keeps_the_early_return(tmp_path):
    """process authority: the sequence ran (pytest exit 1 on a failing test) and its exit status is the verdict; the
    consumer returns without raising, as before."""
    _workspace(tmp_path, manifest=False, tests=TESTS_FAIL)
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        run_res = _validator(tmp_path).run_app_sequence(RUN_PYTEST)
    assert run_res["success"] is False and "environment_reason_code" not in run_res
    assert len(_app_launched(stub.commands)) == 1 and _app_launched(stub.commands)[0][0] == sys.executable
    state = GenerationState()
    assert _raise_runtime_verification_infrastructure_failure(state, run_res, RUN_PYTEST) is None
    assert state.gate_outcomes == []

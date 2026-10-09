"""VERIFICATION-UNIT-ENV-FALLBACK-001 (P2, BACKEND-FINAL-CLOSURE-005 third repair cycle, owner decision 2026-10-09:
FIX narrowly): the deterministic reproducer written BEFORE the production change (ENGINEERING_RULES rule 4).

Measured (C2-S2_A-final generate.log 1652, 1662-1672): the verification-only unit s2's project venv could not be
created (python:3.12-slim, ``ensurepip`` exit 1, cause UNKNOWN); kriya/tools/validate.py logged "falling back to the
default interpreter for this test run" and ran the test gate on the bare container python3 - "No module named
'pytest'" - reported as a code-shaped "TEST FAILURE (verification-only subtask)", which stopped the unit
VERIFICATION_RETRY_NO_CHANGE_POSSIBLE and started an unnecessary verification-owner recovery of s1. The passing rerun
(13:52) created its venv normally and never used the fallback. Host mode has the same degradation with a worse
shape: the substitute is Kriya's own sys.executable, which has pytest and Kriya's dependencies, so a manifest-bearing
project can PASS without its own environment (the shape reproduced here - the only observed fallback run in
production containment failed closed on the missing pytest).

Required invariant: when Kriya requires a project verification environment and its creation fails, the gate reports
a typed ENVIRONMENT failure through the EXISTING taxonomy (``environment_reason_code`` on the gate result, the
``verification_infrastructure_failure`` stop - GRADLE-WRAPPER-CONTAINMENT-001's path): never a test failure, never
Developer repair, never verification-owner recovery, never a closure. Every Python test result carries interpreter
provenance (requested kind, actual kind, path/token, fallback flag) that authorizes nothing by itself. Unchanged:
successful venv creation, the dependency-install error path, dependency-free host projects on the default
interpreter.
"""
import sys
from unittest.mock import AsyncMock, patch

import pytest
from test_workflow import _minimal_attempt_ctx

from kriya.config.config import AutonomyConfig
from kriya.policy.filesystem import WriteScopeMode
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState
from kriya.workflow.verification_coordinator import VerificationCoordinator, VerificationRequest

PYTHON_ENVIRONMENT_UNAVAILABLE = "PYTHON_ENVIRONMENT_UNAVAILABLE"  # the typed code the fix adds beside GRADLE_DISTRIBUTION_UNAVAILABLE
CALC = "def add(a, b):\n    return a + b\n"
TESTS = "import calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n"
ENSUREPIP_FAILURE = ("Error: Command '['.kriya/venv/bin/python3', '-m', 'ensurepip', '--upgrade', '--default-pip']' "
                     "returned non-zero exit status 1.")
TEST_VERIFIER = [{"type": "tool", "tool_name": "test", "description": "run the tests"}]


def _workspace(tmp_path, *, manifest=True):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_calc.py").write_text(TESTS)
    if manifest:
        (root / "requirements.txt").write_text("packaging>=20\n")  # a real declaration: the project requires its own environment
    return root


class _VenvCreationFails:
    """The one stub: the ``python -m venv`` subprocess exits 1 (ensurepip), nothing is created; every other command
    runs for real. ``commands`` records every argv the validator launched. ``method`` is a plain function so that
    ``patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method)`` binds it like the real method
    (a callable object is not a descriptor: it would receive the arguments shifted)."""

    def __init__(self):
        self.commands = []
        real = PolymorphicValidator._run_cmd_with_timeout

        def method(validator, cmd, cwd, **kwargs):
            self.commands.append(list(cmd))
            if len(cmd) >= 3 and cmd[1:3] == ["-m", "venv"]:
                return {"returncode": 1, "stdout": "", "stderr": ENSUREPIP_FAILURE, "timeout": False}
            return real(validator, cmd, cwd, **kwargs)
        self.method = method


def _validator(root):
    return PolymorphicValidator(str(root), original_workspace_path=str(root), autonomy_cfg=AutonomyConfig())


def _ran_pytest(commands):
    return [c for c in commands if len(c) > 2 and c[1] == "-c" and "pytest" in c[2]]


# ---------------------------------------------------------------- the measured shape, host mode: required environment unavailable
def test_p2_2_a_failed_required_venv_creation_is_a_typed_environment_failure_never_a_test_verdict(tmp_path):
    """The project declares dependencies (it requires its own environment); creating it fails. The gate must say so,
    typed, and must not run the tests on Kriya's own interpreter (which would pass here: the false-success shape)."""
    root = _workspace(tmp_path)
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        result = _validator(root).run_tests()
    assert result["success"] is False, ("the gate ran on the fallback interpreter and passed", result.get("output", "")[:200])
    assert result["environment_reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert "ensurepip" in result["output"]  # the creation failure itself is the evidence, not a test's output
    provenance = result["python_interpreter"]
    assert provenance["requested"] == "project_venv"
    assert provenance["actual"] is None and provenance["path"] is None and provenance["fallback"] is False
    assert _ran_pytest(stub.commands) == [], stub.commands  # no test ran: nothing about the candidate was observed


@pytest.mark.asyncio
async def test_p2_2_a_verification_only_unit_stops_typed_on_the_environment_with_no_retry_and_no_owner_recovery(tmp_path):
    """The coordinator raises the existing infrastructure stop (not a test failure); the retry strategy takes
    STOP_ENVIRONMENT: no Developer attempt, and no VERIFICATION_RETRY_NO_CHANGE_POSSIBLE terminal (the controller's
    verification-owner recovery trigger) is declared."""
    root = _workspace(tmp_path)
    outcomes = []
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        coordinator = VerificationCoordinator(_validator(root), record_gate_outcome=outcomes.append,
                                              run_runtime_verification=AsyncMock())
        with pytest.raises(QualityGateFailure) as raised:
            await coordinator.verify(VerificationRequest(required_verification=TEST_VERIFIER, known_files=[], attempt_number=1))
    failure = raised.value.failure
    assert failure.type == "verification_infrastructure_failure", failure.type
    assert failure.diagnostics["reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert "VERIFICATION_INFRASTRUCTURE_FAILURE" in failure.message and "test" not in failure.type
    assert [o["type"] for o in outcomes] == ["verification_infrastructure_failure"], outcomes
    assert _ran_pytest(stub.commands) == []

    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    state.verification_only_inputs = "inputs-digest"  # this was a verification-only attempt (LR-R1-P4 admission)
    state.verification_only_inputs_attempt = 1
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4, write_scope_mode=WriteScopeMode.DENY_ALL,
                               required_verification=TEST_VERIFIER)
    should_break = await handle_attempt_failure(state, ctx, raised.value)
    assert should_break is True
    assert state.environment_failure and state.environment_failure.startswith("VERIFICATION_INFRASTRUCTURE_FAILURE:")
    assert state.no_progress_terminated is False and state.no_progress_reason is None  # never the owner-recovery trigger
    assert state.budgets.last_failure_signature[0] == "verification_infrastructure_failure"  # never the "test" family


# ---------------------------------------------------------------- interpreter provenance (new contract; authorizes nothing)
@pytest.mark.parametrize("shape", ["dependency_free_default", "project_venv_created"])
def test_p2_2_every_python_test_result_carries_interpreter_provenance(tmp_path, shape):
    if shape == "dependency_free_default":
        root = _workspace(tmp_path, manifest=False)
        result = _validator(root).run_tests()
        expected = {"requested": "default", "actual": "default", "path": sys.executable, "fallback": False}
    else:
        root = _workspace(tmp_path)
        # a created venv whose interpreter happens to be Kriya's: the resolver is told creation succeeded
        with patch.object(PolymorphicValidator, "_ensure_project_venv", new=lambda self, args: (sys.executable, None)):
            result = _validator(root).run_tests()
        expected = {"requested": "project_venv", "actual": "project_venv", "path": sys.executable, "fallback": False}
    assert result["success"] is True, result.get("output", "")[:300]
    assert result["python_interpreter"] == expected
    assert "environment_reason_code" not in result


# ---------------------------------------------------------------- negative controls: unchanged behaviour (green before and after)
def test_p2_2_control_a_created_venv_runs_the_tests_as_before(tmp_path):
    root = _workspace(tmp_path)
    stub = _VenvCreationFails()  # records commands; the creation call is never reached when the resolver reports a venv
    with patch.object(PolymorphicValidator, "_ensure_project_venv", new=lambda self, args: (sys.executable, None)), \
         patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        result = _validator(root).run_tests()
    assert result["success"] is True and "environment_reason_code" not in result
    [cmd] = _ran_pytest(stub.commands)
    assert cmd[0] == sys.executable  # the "venv" interpreter the resolver returned


def test_p2_2_control_a_dependency_install_failure_keeps_its_repair_eligible_path(tmp_path):
    """`pip install` of the project's own declared dependencies failed: a real, potentially code-fixable dependency
    problem - the existing install-error verdict, never an environment stop."""
    root = _workspace(tmp_path)
    install_error = "'pip install -r requirements.txt' failed:\nERROR: No matching distribution found for nosuchpkg==9.9"
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_ensure_project_venv", new=lambda self, args: (None, install_error)), \
         patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        result = _validator(root).run_tests()
    assert result["success"] is False and result["output"] == install_error
    assert "environment_reason_code" not in result
    assert _ran_pytest(stub.commands) == []


def test_p2_2_control_a_dependency_free_host_project_runs_on_the_default_interpreter_as_before(tmp_path):
    root = _workspace(tmp_path, manifest=False)
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method):
        result = _validator(root).run_tests()
    assert result["success"] is True and "environment_reason_code" not in result
    [cmd] = _ran_pytest(stub.commands)
    assert cmd[0] == sys.executable
    assert not any(c[1:3] == ["-m", "venv"] for c in stub.commands)  # nothing was requested, nothing fell back

"""``PolymorphicValidator.run_pom_validate`` never reports an unconfirmed validation as a pass.

MEASURED/TRACED at f6194c4 (reconciliation, MVN-VALIDATE-FAIL-OPEN-NEW): the method ended in ``except Exception`` returning
``{"success": True, "output": "mvn validate could not be run (...) - skipped, not confirmed valid."}``, so any unexpected failure
(the command audit's policy denial, a coding error) was a PASS of the ``pom_validate`` gate. Now: an invocation failure (OSError) is
a failed validation, a timeout and a real validation failure are failed validations, a containment setup failure still propagates,
and any other exception propagates (the gate wrapper records it and the attempt handler classifies it); a valid POM and a missing
pom.xml are the only passes. ``_run_maven_cmd`` is the documented patch point."""
from unittest.mock import patch

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import ContainmentSetupError
from kriya.tools.dependency_execution import MAVEN_INVOCATION_FAILED
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.failure import QualityGateFailure

POM = '<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion></project>\n'


def _validator(tmp_path, *, pom=True):
    if pom:
        (tmp_path / "pom.xml").write_text(POM)
    return PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())


def _returns(**fields):
    return {"returncode": 0, "stdout": "", "stderr": "", **fields}


def test_a_valid_pom_passes(tmp_path):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_returns()):
        assert _validator(tmp_path).run_pom_validate() == {"success": True, "output": "Maven POM validation succeeded."}


def test_no_pom_is_the_one_other_pass(tmp_path):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=AssertionError("must not run")):
        assert _validator(tmp_path, pom=False).run_pom_validate()["success"] is True


def test_a_real_validation_failure_fails_with_the_maven_output_and_no_environment_code(tmp_path):
    """F7: only a verdict from Maven itself is a POM validation failure - it carries NO `environment_reason_code`."""
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_returns(returncode=1, stdout="[ERROR] Malformed POM")):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and "Malformed POM" in result["output"] and "environment_reason_code" not in result


def test_a_timeout_stays_a_pom_validation_failure_marked_timed_out_not_an_environment_outcome(tmp_path):
    """ProcessController reports a timeout as returncode -1 + timeout=True (kriya/tools/process.py), not as an exception. Maven STARTED and did not finish; like the compile and test gates
    (a timed-out Maven is a candidate failure there - a POM can itself make Maven hang) it is a validation failure, machine-readable as `timed_out`, with NO environment code."""
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_returns(returncode=-1, timeout=True, stderr="killed")):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and result["timed_out"] is True and "environment_reason_code" not in result
    assert "timed out" in result["output"] and "killed" in result["output"]


@pytest.mark.parametrize("error", [FileNotFoundError("mvn"), PermissionError("mvn is not executable"), OSError("cannot start process")],
                         ids=["not-on-path", "not-executable", "oserror"])
def test_an_invocation_failure_fails_and_names_the_cause(tmp_path, error):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=error):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and result["output"].startswith("Failed to invoke mvn validate: ")
    assert result["environment_reason_code"] == MAVEN_INVOCATION_FAILED, "typed, not only a human-readable string"


@pytest.mark.parametrize("error", [RuntimeError("policy denied the command"), NameError("coding error"), ValueError("unexpected")],
                         ids=["runtime", "coding-error", "value"])
def test_any_other_unexpected_failure_propagates_and_is_never_a_pass(tmp_path, error):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=error):
        with pytest.raises(type(error)):
            _validator(tmp_path).run_pom_validate()


def test_a_containment_setup_failure_still_propagates(tmp_path):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=ContainmentSetupError("daemon unreachable")):
        with pytest.raises(ContainmentSetupError):
            _validator(tmp_path).run_pom_validate()


# ---- one level up: the candidate-gates caller (gate_orchestrator._run_pom_validation_gate) ---------------------------------------
def _gate_inputs(tmp_path):
    from types import SimpleNamespace

    from _strict_doubles import strict_config, strict_kernel

    (tmp_path / "pom.xml").write_text(POM)
    recorded = []
    state = SimpleNamespace(files_written=["pom.xml"], attempt_number=1, record_gate_outcome=recorded.append)
    ctx = SimpleNamespace(worktree_path=str(tmp_path), kernel=strict_kernel(strict_config()))
    return state, ctx, recorded


def test_the_gate_records_a_pom_failure_and_stops_the_attempt(tmp_path):
    from kriya.workflow.failure import QualityGateFailure
    from kriya.workflow.gate_orchestrator import _run_pom_validation_gate  # pylint: disable=protected-access

    state, ctx, recorded = _gate_inputs(tmp_path)
    with patch.object(PolymorphicValidator, "run_pom_validate", return_value={"success": False, "output": "Malformed POM"}):
        with pytest.raises(QualityGateFailure):
            _run_pom_validation_gate(None, state, ctx, None)
    assert len(recorded) == 1


@pytest.mark.parametrize("error", [RuntimeError("policy denied the command"), ValueError("unexpected")], ids=["runtime", "value"])
def test_an_unexpected_failure_of_the_validation_is_never_recorded_as_a_pass_by_the_gate(tmp_path, error):
    from kriya.workflow.gate_orchestrator import _run_pom_validation_gate  # pylint: disable=protected-access

    state, ctx, recorded = _gate_inputs(tmp_path)
    with patch.object(PolymorphicValidator, "run_pom_validate", side_effect=error):
        with pytest.raises(type(error)):
            _run_pom_validation_gate(None, state, ctx, None)
    assert recorded == []                              # no gate outcome, in particular no passing one


def test_the_gate_passes_silently_for_a_confirmed_valid_pom(tmp_path):
    from kriya.workflow.gate_orchestrator import _run_pom_validation_gate  # pylint: disable=protected-access

    state, ctx, recorded = _gate_inputs(tmp_path)
    with patch.object(PolymorphicValidator, "run_pom_validate", return_value={"success": True, "output": "ok"}):
        assert _run_pom_validation_gate(None, state, ctx, None) is None
    assert recorded == []


# ---- F7: the gate attributes the failure to the right owner ---------------------------------------------------------------------
def _raise_from_gate(tmp_path, result):
    from kriya.workflow.gate_orchestrator import _run_pom_validation_gate  # pylint: disable=protected-access

    state, ctx, recorded = _gate_inputs(tmp_path)
    with patch.object(PolymorphicValidator, "run_pom_validate", return_value=result):
        with pytest.raises(QualityGateFailure) as raised:
            _run_pom_validation_gate(None, state, ctx, None)
    return raised.value.failure, recorded


def test_a_maven_that_could_not_run_is_a_verification_infrastructure_failure_not_a_pom_defect(tmp_path):
    failure, recorded = _raise_from_gate(tmp_path, {"success": False, "output": "Failed to invoke mvn validate: [Errno 2]", "environment_reason_code": MAVEN_INVOCATION_FAILED})
    assert failure.type == "verification_infrastructure_failure" and failure.diagnostics["reason_code"] == MAVEN_INVOCATION_FAILED
    assert "could not start" in failure.message and "not a code failure" in failure.message and "POM VALIDATION FAILED" not in failure.message
    assert not failure.failed_content and not failure.likely_files and not failure.file_locations   # nothing offered to the Developer to repair
    assert [outcome["type"] for outcome in recorded] == ["verification_infrastructure_failure"]


def test_a_real_pom_defect_stays_a_pom_semantic_validation_with_the_pom_offered_for_repair(tmp_path):
    failure, recorded = _raise_from_gate(tmp_path, {"success": False, "output": "[ERROR] Malformed POM"})
    assert failure.type == "pom_semantic_validation" and "POM VALIDATION FAILED for pom.xml" in failure.message
    assert failure.failed_content == {"pom.xml": POM} and failure.likely_files == ["pom.xml"]
    assert [outcome["type"] for outcome in recorded] == ["pom_semantic_validation"]


def test_a_timed_out_maven_is_offered_to_the_developer_as_a_pom_validation_failure(tmp_path):
    """A timed-out Maven is a candidate failure like the compile / test gates' (a POM can make Maven hang): the POM is offered for repair."""
    failure, _recorded = _raise_from_gate(tmp_path, {"success": False, "timed_out": True, "output": "Maven POM validation timed out (no verdict within its time bound):\n"})
    assert failure.type == "pom_semantic_validation" and failure.failed_content == {"pom.xml": POM} and "timed out" in failure.message


def test_the_env_gate_wording_of_existing_codes_is_unchanged(tmp_path):
    """Every earlier environment code still reads 'could not start' (the recorded goldens depend on it)."""
    from kriya.workflow.gate_orchestrator import _stop_on_environment_gate_result  # pylint: disable=protected-access

    state, _ctx, _recorded = _gate_inputs(tmp_path)
    state.attempt_number = 1
    with pytest.raises(QualityGateFailure) as raised:
        _stop_on_environment_gate_result(state, {"environment_reason_code": "GRADLE_DISTRIBUTION_UNAVAILABLE", "output": "x"}, "compile")
    assert "verification tool could not start (GRADLE_DISTRIBUTION_UNAVAILABLE)" in raised.value.failure.message


@pytest.mark.asyncio
@pytest.mark.parametrize("result,stops", [({"success": False, "output": "x", "environment_reason_code": MAVEN_INVOCATION_FAILED}, True),
                                          ({"success": False, "output": "[ERROR] Malformed POM"}, False)], ids=["infrastructure-stops", "pom-defect-retries"])
async def test_the_retry_strategy_stops_the_run_on_the_environment_and_retries_a_pom_defect(tmp_path, result, stops):
    """Developer-facing attribution through the real retry strategy: an unrunnable Maven is STOP_ENVIRONMENT (no repair attempt is asked of the Developer); a real POM defect
    is an ordinary repairable failure."""
    from test_workflow import _minimal_attempt_ctx

    from kriya.workflow.retry_strategy import handle_attempt_failure
    from kriya.workflow.state import GenerationState

    failure, _recorded = _raise_from_gate(tmp_path, result)
    state = GenerationState()
    state.attempt_number = 1
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    should_break = await handle_attempt_failure(state, ctx, QualityGateFailure(failure))
    assert should_break is stops
    assert bool(state.environment_failure) is stops

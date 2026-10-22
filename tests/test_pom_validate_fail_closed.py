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
from kriya.tools.validate import PolymorphicValidator

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


def test_a_real_validation_failure_fails_with_the_maven_output(tmp_path):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_returns(returncode=1, stdout="[ERROR] Malformed POM")):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and "Malformed POM" in result["output"]


def test_a_timeout_fails(tmp_path):
    """ProcessController reports a timeout as returncode -1 + timeout=True (kriya/tools/process.py), not as an exception."""
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_returns(returncode=-1, timeout=True)):
        assert _validator(tmp_path).run_pom_validate()["success"] is False


@pytest.mark.parametrize("error", [FileNotFoundError("mvn"), PermissionError("mvn is not executable"), OSError("cannot start process")],
                         ids=["not-on-path", "not-executable", "oserror"])
def test_an_invocation_failure_fails_and_names_the_cause(tmp_path, error):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=error):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and result["output"].startswith("Failed to invoke mvn validate: ")


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

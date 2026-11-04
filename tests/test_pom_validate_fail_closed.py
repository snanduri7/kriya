"""``PolymorphicValidator.run_pom_validate`` never reports an unconfirmed validation as a pass, and never convicts the POM without a trustworthy verdict.

MEASURED/TRACED at f6194c4 (reconciliation, MVN-VALIDATE-FAIL-OPEN-NEW): the method ended in ``except Exception`` returning
``{"success": True, "output": "mvn validate could not be run (...) - skipped, not confirmed valid."}``, so any unexpected failure
(the command audit's policy denial, a coding error) was a PASS of the ``pom_validate`` gate. Now a containment setup failure and any
other exception propagate (the gate wrapper records it and the attempt handler classifies it); a valid POM and a missing pom.xml are the only passes.

F7 (owner decision, Wave-1 closure delta). Invariant: ``no trustworthy validator verdict -> no candidate conviction``. ``pom_semantic_validation`` (the POM offered to the
Developer for repair) is reserved for a Maven that RAN and rejected the POM with trustworthy model diagnostics. Everything else that fails - the executable missing or not
executable, a timeout, a signal kill, resolution / transfer evidence (alone or mixed with model messages), a non-zero exit with no interpretable diagnostics - is an environment
outcome (``environment_reason_code``) raised as the existing ``verification_infrastructure_failure``, with the captured output preserved. MEASURED before (6cde784): PermissionError,
timeout, SIGKILL and empty-output exits all retried the Developer with the POM offered. The positive and negative controls are REAL recorded Maven output:
tests/fixtures/maven_validate (Apache Maven 3.9.16 run for this test), tests/fixtures/oracle_preflight/env-T2-empty_maven_repo.txt and excerpts of two live Kriya runs.
``_run_maven_cmd`` is the documented patch point."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from _hermetic_path import hermetic_path

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import ContainmentSetupError
from kriya.tools.dependency_execution import (
    MAVEN_INVOCATION_FAILED,
    MAVEN_VALIDATE_NO_DIAGNOSTICS,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID,
    MAVEN_VALIDATE_RESOLUTION_UNVERIFIED,
    MAVEN_VALIDATE_TERMINATED,
    MAVEN_VALIDATE_TIMED_OUT,
)
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


FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORDED = json.loads((FIXTURES / "maven_validate" / "recorded_mvn_validate.json").read_text(encoding="utf-8"))
LIVE = json.loads((FIXTURES / "maven_validate" / "recorded_live_failures.json").read_text(encoding="utf-8"))
ENV_T2 = (FIXTURES / "oracle_preflight" / "env-T2-empty_maven_repo.txt").read_text(encoding="utf-8")


def _recorded(name):
    return _returns(returncode=RECORDED[name]["returncode"], stdout=RECORDED[name]["stdout"], stderr=RECORDED[name]["stderr"])


@pytest.mark.parametrize("name", ["wrong_root_element", "malformed_xml", "missing_dependency_version"])
def test_a_maven_that_ran_and_rejected_the_pom_with_model_diagnostics_convicts_it(tmp_path, name):
    """REAL `mvn -B -o validate` output: a located POM-model problem with no resolution evidence is the one trustworthy rejection."""
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_recorded(name)):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Maven POM validation failed:")
    assert RECORDED[name]["stdout"].strip().splitlines()[-1] in result["output"] or "[ERROR]" in result["output"]


def test_the_recorded_valid_pom_passes(tmp_path):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_recorded("valid")):
        assert _validator(tmp_path).run_pom_validate()["success"] is True


NO_VERDICT = [
    ("unresolvable-parent-offline (REAL)", _returns(returncode=RECORDED["unresolvable_parent_offline"]["returncode"], stdout=RECORDED["unresolvable_parent_offline"]["stdout"],
                                                    stderr=RECORDED["unresolvable_parent_offline"]["stderr"]), MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    ("model-problems-cascading-from-an-unresolved-bom (REAL env-T2)", _returns(returncode=1, stdout=ENV_T2), MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    ("transfer-failure-name-resolution (REAL live run)", _returns(returncode=1, stdout=LIVE["demo01_transfer_failure"]["output"]), MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    ("non-resolvable-parent-offline-in-a-plugin (REAL live run)", _returns(returncode=1, stdout=LIVE["sec001_non_resolvable_parent_offline"]["output"]), MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    ("non-resolvable-parent-transfer-timed-out", _returns(returncode=1, stderr="[ERROR] Non-resolvable parent POM for g:a:1: Could not transfer artifact g:p:pom:1 from/to central: Connect timed out"),
     MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    # the existing canonical offline patterns (dependency_execution._MAVEN_OFFLINE_MISSING_RE, measured earlier) are reused, not re-listed
    ("negative-cached-resolution (existing offline pattern)", _returns(returncode=1, stderr="[ERROR] g:p:pom:1 was cached in the local repository, resolution will not be reattempted until the update interval of central has elapsed"),
     MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    ("plugin-prefix-needs-metadata (existing offline pattern)", _returns(returncode=1, stderr="[ERROR] No plugin found for prefix 'exec' in the current project and in the plugin groups"), MAVEN_VALIDATE_RESOLUTION_UNVERIFIED),
    ("timeout", _returns(returncode=-1, timeout=True, stdout="partial out", stderr="[TIMEOUT] Command timed out after 120 seconds."), MAVEN_VALIDATE_TIMED_OUT),
    ("killed-by-a-signal", _returns(returncode=-9), MAVEN_VALIDATE_TERMINATED),
    ("exit-1-with-no-output", _returns(returncode=1), MAVEN_VALIDATE_NO_DIAGNOSTICS),
    ("container-kill-exit-137", _returns(returncode=137), MAVEN_VALIDATE_NO_DIAGNOSTICS),
    ("exit-1-with-uninterpretable-text", _returns(returncode=1, stderr="Segmentation fault: 11"), MAVEN_VALIDATE_NO_DIAGNOSTICS),
]


@pytest.mark.parametrize("label,maven,code", NO_VERDICT, ids=[n[0] for n in NO_VERDICT])
def test_no_trustworthy_verdict_is_an_environment_outcome_with_the_captured_diagnostics_preserved(tmp_path, label, maven, code):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=maven):
        result = _validator(tmp_path).run_pom_validate()
    assert result["success"] is False and result["environment_reason_code"] == code and code in MAVEN_VALIDATE_NO_VERDICT_CODES
    assert result["returncode"] == maven["returncode"]
    for captured in (maven["stdout"].strip(), maven["stderr"].strip()):
        assert captured in result["output"], "the captured diagnostics are preserved"
    assert result.get("timed_out", False) is bool(maven.get("timeout"))


def test_exit_zero_is_the_success_whatever_the_text_says(tmp_path):
    """Maven's exit status is its verdict; the classifier is only ever consulted for a non-zero exit."""
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_returns(returncode=0, stdout="[ERROR] Could not transfer artifact x (a warning Maven recovered from)")):
        assert _validator(tmp_path).run_pom_validate()["success"] is True


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


@pytest.mark.parametrize("code,phrase", [(MAVEN_INVOCATION_FAILED, "could not start"), (MAVEN_VALIDATE_TIMED_OUT, "returned no trustworthy verdict"),
                                         (MAVEN_VALIDATE_RESOLUTION_UNVERIFIED, "returned no trustworthy verdict"), (MAVEN_VALIDATE_TERMINATED, "returned no trustworthy verdict"),
                                         (MAVEN_VALIDATE_NO_DIAGNOSTICS, "returned no trustworthy verdict"), (MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID, "returned no trustworthy verdict")])
def test_a_maven_without_a_verdict_is_a_verification_infrastructure_failure_not_a_pom_defect(tmp_path, code, phrase):
    failure, recorded = _raise_from_gate(tmp_path, {"success": False, "output": "Failed to invoke mvn validate: [Errno 2]", "environment_reason_code": code})
    assert failure.type == "verification_infrastructure_failure" and failure.diagnostics["reason_code"] == code
    assert phrase in failure.message and "not a code failure" in failure.message and "POM VALIDATION FAILED" not in failure.message
    assert "Failed to invoke mvn validate" in failure.message, "the captured diagnostics reach the failure"
    assert not failure.failed_content and not failure.likely_files and not failure.file_locations   # nothing offered to the Developer to repair
    assert [outcome["type"] for outcome in recorded] == ["verification_infrastructure_failure"]


def test_a_real_pom_defect_stays_a_pom_semantic_validation_with_the_pom_offered_for_repair(tmp_path):
    failure, recorded = _raise_from_gate(tmp_path, {"success": False, "output": "[ERROR] Malformed POM"})
    assert failure.type == "pom_semantic_validation" and "POM VALIDATION FAILED for pom.xml" in failure.message
    assert failure.failed_content == {"pom.xml": POM} and failure.likely_files == ["pom.xml"]
    assert [outcome["type"] for outcome in recorded] == ["pom_semantic_validation"]


def test_the_env_gate_wording_of_existing_codes_is_unchanged(tmp_path):
    """Every earlier environment code still reads 'could not start' (the recorded goldens depend on it)."""
    from kriya.workflow.gate_orchestrator import _stop_on_environment_gate_result  # pylint: disable=protected-access

    state, _ctx, _recorded = _gate_inputs(tmp_path)
    state.attempt_number = 1
    with pytest.raises(QualityGateFailure) as raised:
        _stop_on_environment_gate_result(state, {"environment_reason_code": "GRADLE_DISTRIBUTION_UNAVAILABLE", "output": "x"}, "compile")
    assert "verification tool could not start (GRADLE_DISTRIBUTION_UNAVAILABLE)" in raised.value.failure.message


def _validate_result(tmp_path, maven):
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=maven):
        return _validator(tmp_path).run_pom_validate()


@pytest.mark.asyncio
@pytest.mark.parametrize("label,maven,stops", [("model-rejection (REAL)", _recorded("wrong_root_element"), False), ("missing-version (REAL)", _recorded("missing_dependency_version"), False)]
                         + [(label, maven, True) for label, maven, _code in NO_VERDICT], ids=["model-rejection-REAL", "missing-version-REAL"] + [n[0] for n in NO_VERDICT])
async def test_the_retry_strategy_stops_the_run_on_the_environment_and_retries_a_pom_defect(tmp_path, label, maven, stops):
    """Developer-facing attribution through the real retry strategy: an unrunnable Maven is STOP_ENVIRONMENT (no repair attempt is asked of the Developer); a real POM defect
    is an ordinary repairable failure."""
    from test_workflow import _minimal_attempt_ctx

    from kriya.workflow.retry_strategy import handle_attempt_failure
    from kriya.workflow.state import GenerationState

    failure, _recorded = _raise_from_gate(tmp_path, _validate_result(tmp_path, maven))
    state = GenerationState()
    state.attempt_number = 1
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    should_break = await handle_attempt_failure(state, ctx, QualityGateFailure(failure))
    assert should_break is stops
    assert bool(state.environment_failure) is stops


# ---- the real process path: a fake `mvn` the CONTAINED launcher resolves, the real ProcessController / validator / gate (no stub of _run_maven_cmd's result) --------------------
# Under the default contained config `mvn` is resolved by the contained launch (the test stand-in's launcher here, the image's launch stage in a real container), never by a host
# spawn. `executable-missing` / `not-executable` therefore exercise the backend's typed never-started fact (FINITE-LAUNCH-TARGET-START-001): the launcher's own failed exec, not an
# exit code. The real-Docker equivalent, with a Maven image, is tests/test_finite_launch_target_start.py.
def _fake_mvn(tmp_path, script, *, executable=True):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if script is not None:
        mvn = bindir / "mvn"
        mvn.write_text(script)
        mvn.chmod(0o755 if executable else 0o644)
    (tmp_path / "pom.xml").write_text(POM)
    # PATH is the fake bin dir plus `sleep` only: a host `mvn` elsewhere on PATH (a hosted runner image has one) must not answer for a missing or non-executable fake.
    return f"{bindir}:{hermetic_path(tmp_path / 'tools', ('sleep',), absent=('mvn',))}"


REAL_PROCESS = [
    ("exits-1-with-model-diagnostics", "#!/bin/sh\necho \"[ERROR] Malformed POM pom.xml: Expected root element 'project' but found 'plugin'\" >&2\nexit 1\n", True, None, False),
    ("executable-missing", None, True, MAVEN_INVOCATION_FAILED, False),
    ("not-executable", "#!/bin/sh\nexit 0\n", False, MAVEN_INVOCATION_FAILED, False),
    ("timeout", "#!/bin/sh\necho partial\nsleep 30\n", True, MAVEN_VALIDATE_TIMED_OUT, True),
    ("sigkill", "#!/bin/sh\nkill -9 $$\n", True, MAVEN_VALIDATE_TERMINATED, False),
    ("exit-1-silent", "#!/bin/sh\nexit 1\n", True, MAVEN_VALIDATE_NO_DIAGNOSTICS, False),
]


@pytest.mark.parametrize("label,script,executable,code,timed_out", REAL_PROCESS, ids=[r[0] for r in REAL_PROCESS])
def test_the_real_process_path_attributes_each_condition_to_the_right_owner(tmp_path, monkeypatch, label, script, executable, code, timed_out):
    monkeypatch.setenv("PATH", _fake_mvn(tmp_path, script, executable=executable))
    original = PolymorphicValidator._run_maven_cmd

    def short(self, goals, cwd, timeout=300, **kwargs):          # the real 120 s bound, shortened so the test finishes
        return original(self, goals, cwd=cwd, timeout=2 if label == "timeout" else timeout, **kwargs)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", short):
        result = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig()).run_pom_validate()
        failure, _recorded = _raise_from_gate(tmp_path, result)
    assert result["success"] is False and result.get("environment_reason_code") == code and bool(result.get("timed_out")) is timed_out
    assert failure.type == ("pom_semantic_validation" if code is None else "verification_infrastructure_failure")
    assert bool(failure.failed_content) is (code is None)          # the POM is offered for repair only when Maven itself rejected it
    if label == "timeout":
        assert "partial" in result["output"], "the output captured before the kill is preserved"

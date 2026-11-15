"""R-13 VERIFICATION-ENVIRONMENT-VERDICT (architecture closure S0; registry WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001).

Invariant: container / environment unavailable -> a typed ENVIRONMENT verdict -> zero candidate / retry charge -> never a gate or
product failure. A failed gate is a statement about the candidate only when the gate's tool RAN.

MEASURED on ef89dcd (2026-11-15, scripted refusal at the launch seam, no container daemon): a backend refusal that is not the one
typed bind-source case (`docker: Error response from daemon: ...`, exit 125, target never started) came back from the Python
compile gate as ``{"success": False, "output": <docker text>}`` with no ``environment_reason_code`` - an ordinary COMPILATION
FAILURE, charged to the candidate and offered to the Developer. The same held for a refused pytest launch, a refused acceptance
runner, a refused ``pip install`` (reported as a dependency problem of the candidate) and the Ruby gates. The Java adapters, the
POM validation, venv creation and the application run already typed it.

The refusal is scripted with the production launcher contract (tests/test_bind_source_relaunch_001.py::_ScriptedBackend): the
real ProcessController, the real finite-launch marker protocol and the real validator run; only the backend's ``prepare`` is
scripted. Nothing here needs Docker.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest
import test_non_candidate_failure_scoped_reset_001 as reset_harness
from test_bind_source_diagnostic_001 import _LAUNCHER, _REFUSAL, _profile
from test_bind_source_relaunch_001 import (
    OTHER_DOCKER_ERROR,
    _bind_source,
    _ScriptedBackend,
    _State,
    _validator,
    _workspace,
)

from kriya.tools import service_runtime
from kriya.tools.containment import (
    BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST,
    BACKEND_TARGET_NOT_STARTED,
    ENVIRONMENT_NOT_STARTED_CODES,
    LAUNCHER_REACHED_MARKER,
    TARGET_NOT_STARTED_MARKER,
    FiniteLaunchStage,
    PreparedContainment,
)
from kriya.tools.dependency_execution import PYTHON_ENVIRONMENT_UNAVAILABLE
from kriya.tools.process import ProcessController
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.failure import ENVIRONMENT_VERDICT_FAILURE_TYPES, Failure
from kriya.workflow.gate_orchestrator import QualityGateFailure, _stop_on_environment_gate_result
from kriya.workflow.open_findings import is_candidate_finding, replaces_open_finding
from kriya.workflow.retry_policy import RetryAction, charge_failed_attempt
from kriya.workflow.state import RetryBudgets

TEST_FILE = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


class _SelectiveBackend:
    """Refuses exactly the launches ``refuse`` selects (by the command about to run); every other launch runs for real."""
    name = "scripted-oci"

    def __init__(self, refuse):
        self.refuse, self.prepared = refuse, []

    def prepare(self, profile, command):
        self.prepared.append(list(command))
        stage = FiniteLaunchStage(prefix=(sys.executable, "-c", _LAUNCHER, "n1"), marker=f"{TARGET_NOT_STARTED_MARKER}n1", reached=f"{LAUNCHER_REACHED_MARKER}n1")
        if self.refuse(list(command)):
            return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage,
                                       command_prefix=(sys.executable, "-c", _REFUSAL, OTHER_DOCKER_ERROR))
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage)


def _gate_workspace(tmp_path: Path) -> Path:
    workspace = _workspace(tmp_path)
    (workspace / "test_calc.py").write_text(TEST_FILE, encoding="utf-8")
    return workspace


def _ready_interpreter(validator: PolymorphicValidator, monkeypatch) -> None:
    """The project environment already exists: the launch under test is the gate's own tool, not the environment's creation."""
    monkeypatch.setattr(validator, "_resolve_python_interpreter", lambda: (sys.executable, None))


def _python_compile(validator, workspace, monkeypatch):
    return validator.run_compile_check(["calc.py"])


def _python_tests(validator, workspace, monkeypatch):
    _ready_interpreter(validator, monkeypatch)
    return validator.run_tests()


def _acceptance(validator, workspace, monkeypatch):
    _ready_interpreter(validator, monkeypatch)
    (workspace / "runner.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    return validator.run_acceptance("runner.py", [])


def _pom_validate(validator, workspace, monkeypatch):
    (workspace / "pom.xml").write_text("<project/>", encoding="utf-8")
    return validator.run_pom_validate()


def _ruby_compile(validator, workspace, monkeypatch):
    (workspace / "calc.rb").write_text("def add(a, b) = a + b\n", encoding="utf-8")
    validator.stack = "ruby"
    return validator.run_compile_check(["calc.rb"])


def _ruby_tests(validator, workspace, monkeypatch):
    validator.stack = "ruby"
    return validator.run_tests()


def _ruby_bundle_install(validator, workspace, monkeypatch):
    (workspace / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    validator.stack = "ruby"
    return validator.run_tests()


GATES = {"python_compile": _python_compile, "python_tests": _python_tests, "acceptance": _acceptance, "pom_validate": _pom_validate,
         "ruby_compile": _ruby_compile, "ruby_tests": _ruby_tests, "ruby_bundle_install": _ruby_bundle_install}


@pytest.mark.parametrize("gate", sorted(GATES))
def test_a_refused_launch_is_a_typed_environment_verdict_at_every_validator_gate(tmp_path, monkeypatch, gate):
    """The original symptom, per launch site: the backend refuses to start the gate's container for a reason no diagnostic names."""
    backend = _ScriptedBackend(*([OTHER_DOCKER_ERROR] * 8))
    workspace = _gate_workspace(tmp_path)
    validator = _validator(workspace, monkeypatch, backend)

    result = GATES[gate](validator, workspace, monkeypatch)

    assert backend.prepared, "the gate must have reached the launch seam (else this proves nothing)"
    assert result["success"] is False, "a tool that never ran can never pass a gate"
    assert result.get("environment_reason_code") in ENVIRONMENT_NOT_STARTED_CODES | {"MAVEN_INVOCATION_FAILED"}, (
        f"{gate}: a never-started tool came back as an ordinary gate failure: {result}")
    # ... and the orchestrator stops on it as an infrastructure failure, never as a compile / test failure of the candidate.
    state = _State()
    with pytest.raises(QualityGateFailure) as stop:
        _stop_on_environment_gate_result(state, result, gate)
    failure = stop.value.failure
    assert failure.type == "verification_infrastructure_failure" and "not a code failure" in failure.message
    assert replaces_open_finding(failure) is False, "an environment verdict never opens a finding or a failure family: nothing is charged to the candidate"


def test_the_generic_code_is_used_only_when_the_backend_names_no_diagnostic(tmp_path, monkeypatch):
    """A refusal the containment owner did classify keeps its own code (the absent bind source), and is typed too."""
    absent = _ScriptedBackend(_bind_source(tmp_path, present=False))
    result = _validator(_workspace(tmp_path), monkeypatch, absent).run_compile_check(["calc.py"])
    assert result["success"] is False and result["environment_reason_code"] == BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST

    other = _ScriptedBackend(OTHER_DOCKER_ERROR)
    (tmp_path / "second").mkdir()
    result = _validator(_workspace(tmp_path / "second"), monkeypatch, other).run_compile_check(["calc.py"])
    assert result["environment_reason_code"] == BACKEND_TARGET_NOT_STARTED and result["output"].startswith(BACKEND_TARGET_NOT_STARTED)
    assert OTHER_DOCKER_ERROR.splitlines()[0] in result["output"], "the backend's own text stays in the evidence"


@pytest.mark.parametrize("source, expected", [("def add(a, b):\n    return a + b\n", True), ("def add(a, b)\n    return a + b\n", False)])
def test_negative_control_a_started_tool_is_judged_by_its_own_output(tmp_path, monkeypatch, source, expected):
    """The verdict of a tool that DID start is untouched in both directions: a real compile error stays a candidate failure."""
    backend = _ScriptedBackend()
    result = _validator(_workspace(tmp_path, source), monkeypatch, backend).run_compile_check(["calc.py"])
    assert result["success"] is expected and "environment_reason_code" not in result


def test_negative_control_the_never_started_fact_is_required_not_an_exit_status_or_text(tmp_path):
    """Exit 125 and the daemon's text from a target that STARTED (a program may print anything) are an ordinary failure."""
    started = {"returncode": 125, "stdout": "", "stderr": OTHER_DOCKER_ERROR}
    assert "environment_reason_code" not in PolymorphicValidator._validation_result(False, OTHER_DOCKER_ERROR, started)
    assert "environment_reason_code" not in PolymorphicValidator._validation_result(False, OTHER_DOCKER_ERROR, {**started, "target_started": True})
    never = PolymorphicValidator._validation_result(True, "", {**started, "returncode": 0, "target_started": False})
    assert never["success"] is False and never["environment_reason_code"] == BACKEND_TARGET_NOT_STARTED, "even a zero exit status proves nothing when the target never started"


def test_a_refused_dependency_install_is_the_environment_not_a_dependency_problem_of_the_candidate(tmp_path, monkeypatch):
    """``install_error`` means pip itself rejected the declared dependencies (possibly code-fixable). A pip that never started judged nothing."""
    workspace = _gate_workspace(tmp_path)
    backend = _SelectiveBackend(lambda command: "pip" in command)
    validator = PolymorphicValidator(str(workspace))
    profile = _profile(workspace)
    monkeypatch.setattr(validator, "build_containment_profile_and_backend", lambda **_kwargs: (profile, backend))

    with validator.verification_session("r13"):
        _interpreter, install_error = validator._resolve_python_interpreter()
        result = validator.run_tests()

    assert any("pip" in command for command in backend.prepared), "the install launch must have been reached"
    assert install_error is None, f"a never-started pip was reported as a dependency failure of the candidate: {install_error}"
    assert validator.python_environment_error is not None and validator.python_environment_error[0] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert result["success"] is False and result["environment_reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert BACKEND_TARGET_NOT_STARTED in result["output"]


# ---- zero candidate / retry charge ---------------------------------------------------------------------------------------------------

def test_an_environment_verdict_is_never_a_finding_about_the_candidate():
    """MEASURED before the fix: ``verification_infrastructure_failure`` was a candidate finding (it replaced the open finding and could
    open a new failure family, replenishing the scoped budgets, on its way to the environment stop)."""
    assert ENVIRONMENT_VERDICT_FAILURE_TYPES == {"verification_infrastructure_failure", "test_verification_infrastructure_failure", "containment_setup_failed"}
    for failure_type in ENVIRONMENT_VERDICT_FAILURE_TYPES:
        assert is_candidate_finding(failure_type) is False, failure_type
    for control in ("compilation", "test_failure", "regression_unattributed"):  # negative control: statements about the candidate stay so
        assert is_candidate_finding(control) is True, control


@pytest.mark.parametrize("mode", [None, RetryAction.TARGETED.value, RetryAction.MISSING_FILES.value, RetryAction.FALLBACK_TARGETED.value,
                                  RetryAction.API_CONTRACT_RECOVERY.value])
def test_an_environment_verdict_charges_no_retry_budget_in_any_attempt_mode(mode):
    def charged(environment_verdict: bool) -> RetryBudgets:
        budgets = RetryBudgets()
        charge_failed_attempt(budgets, attempt_mode=mode, plan_scope_conflict=False, failure_family_changed=False,
                              environment_verdict=environment_verdict)
        return budgets

    assert charged(True) == RetryBudgets(), "nothing about the candidate was observed: nothing is charged"
    if mode != RetryAction.FALLBACK_TARGETED.value:  # that mode is bounded by its own one-shot flag and never charged a counter
        assert charged(False) != RetryBudgets(), "negative control: an ordinary failed attempt in this mode is charged"


# ---- the launch site outside the validator: artifact preparation of a runtime / service check ---------------------------------------------

def _service_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "svc"
    workspace.mkdir()
    (workspace / "pom.xml").write_text("<project/>", encoding="utf-8")
    return workspace


@pytest.mark.parametrize("route", ["controller", "contained_build"])
def test_a_never_started_artifact_build_is_not_evidence_about_the_project(tmp_path, route):
    """runtime_verification reads a preparation outcome with BOTH a command and a return code as "the build ran and failed"
    (a ``compile`` failure of the candidate) and one with no return code as Kriya's own infrastructure failure. MEASURED before
    the fix: a refused ``mvn package`` container (exit 125, target never started) came back with return code 125."""
    workspace = _service_workspace(tmp_path)
    command = ["java", "-jar", "target/app.jar"]
    if route == "controller":
        outcome = service_runtime._prepare_required_artifact(
            command, str(workspace), controller=ProcessController(), containment_profile=_profile(workspace),
            containment_backend=_ScriptedBackend(OTHER_DOCKER_ERROR))
    else:
        refused = ProcessController().run(["mvn", "package"], cwd=str(workspace), timeout=30, containment_profile=_profile(workspace),
                                          containment_backend=_ScriptedBackend(OTHER_DOCKER_ERROR)).to_dict()
        assert refused["target_started"] is False, "the scripted refusal must be the typed never-started fact"
        outcome = service_runtime._prepare_required_artifact(
            command, str(workspace), controller=ProcessController(), contained_build=lambda _argv, _timeout: refused)

    assert outcome.outcome == service_runtime.ServiceVerificationOutcomeKind.PREPARATION_FAILED
    assert outcome.returncode is None, "a build that never started has no return code: it is never 'the build ran and failed'"
    assert BACKEND_TARGET_NOT_STARTED in outcome.reasoning and OTHER_DOCKER_ERROR.splitlines()[0] in outcome.stderr


def test_negative_control_a_build_that_ran_and_failed_keeps_its_return_code(tmp_path):
    workspace = _service_workspace(tmp_path)
    ran = {"returncode": 1, "stdout": "", "stderr": "[ERROR] COMPILATION ERROR", "timeout": False}
    outcome = service_runtime._prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(workspace), controller=ProcessController(), contained_build=lambda _argv, _timeout: ran)
    assert outcome.outcome == service_runtime.ServiceVerificationOutcomeKind.PREPARATION_FAILED and outcome.returncode == 1


def test_a_scanner_that_produced_nothing_is_a_typed_tool_status_never_a_clean_scan_or_a_finding():
    """The static-analysis launch site: a scan whose container never started leaves no output and the launcher's exit status. The
    adapter's own result for exactly that is a typed non-COMPLETE status with no finding - never "clean", never a candidate finding."""
    from kriya.static_analysis.adapters.semgrep import interpret_output
    from kriya.static_analysis.model import ScanStatus

    result = interpret_output("", 125, ["calc.py"], frozenset(), "/workspace")
    assert result.status is ScanStatus.MALFORMED_OUTPUT and result.status is not ScanStatus.COMPLETE
    assert not result.findings and result.reason_code


def _environment_stop():
    """What ``_stop_on_environment_gate_result`` raises for a never-started tool: its own, never-seen text."""
    raw = (f"VERIFICATION_INFRASTRUCTURE_FAILURE: the tests gate's verification tool could not start ({BACKEND_TARGET_NOT_STARTED}); "
           f"nothing about the candidate was observed, so this is not a code failure.\n{OTHER_DOCKER_ERROR}")
    return QualityGateFailure(Failure(type="verification_infrastructure_failure", message=raw, raw_output=raw, source="orchestrator",
                                      diagnostics={"reason_code": BACKEND_TARGET_NOT_STARTED, "gate": "tests"}))


def _candidate_failure():
    """Negative control: a genuinely different statement about the candidate, recorded the same way."""
    return reset_harness._gate(reset_harness.COMPILE.replace("UnsupportedEncodingException", "FileNotFoundException"), (12,))


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", [True, False])
async def test_the_real_recorder_charges_and_replenishes_nothing_for_an_environment_verdict(tmp_path, environment):
    """The whole recording path (retry_strategy.handle_attempt_failure) over run 10's first five attempts, then the sixth: an environment
    verdict leaves the open finding, the failure family and every budget exactly as they were; a candidate failure in its place
    (the control) replaces the finding and is charged."""
    run = await reset_harness._Run(tmp_path).play(reset_harness.RUN10_TO_ATTEMPT_5)
    before = run.rows[-1]
    budgets_before = dataclasses.replace(run.state.budgets, seen_failure_signatures=set(run.state.budgets.seen_failure_signatures))

    await run.play([(_environment_stop if environment else _candidate_failure, None)])
    after = run.rows[-1]

    if environment:
        assert after.findings == before.findings, "an environment verdict never replaces the open finding"
        assert after.family == before.family and run.resets == [2], "... never opens a failure family or replenishes a scoped budget"
        # fallback_attempts_used is the DISPATCH accounting of the attempt that was made (attempt.py; the harness mirrors it before the
        # failure is recorded): a model call did happen. Everything the failure recorder owns is unchanged.
        recorded = dataclasses.replace(budgets_before, fallback_attempts_used=run.state.budgets.fallback_attempts_used)
        assert run.state.budgets == recorded, "... and charges no budget"
    else:
        assert after.findings != before.findings and run.state.budgets != budgets_before

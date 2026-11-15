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
import time
from pathlib import Path

import pytest
import test_non_candidate_failure_scoped_reset_001 as reset_harness
import test_service_runtime as service_harness
from test_bind_source_diagnostic_001 import _LAUNCHER, _REFUSAL, _profile
from test_bind_source_relaunch_001 import (
    OTHER_DOCKER_ERROR,
    _bind_source,
    _ScriptedBackend,
    _State,
    _validator,
    _workspace,
)
from test_finite_launch_target_start import _SIMULATED_LAUNCHER

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


# ---- the managed (long-running) service launch: R13-MANAGED-SERVICE-LAUNCH-REFUSAL-NOT-TYPED-001 ----------------------------------------
#
# MEASURED on ec2bec1 (2026-11-15, the scripted refusal below, no daemon): ``run_managed_service_verification`` answered a launch the backend
# refused with ``SERVICE_EXITED_BEFORE_READY``, return code 125 and "Service process exited with code 125 before becoming ready" - a statement
# that the candidate's service ran and exited. A managed launch had no launch stage, so the fact did not exist. It now goes through the SAME
# stage a finite command gets; kriya/tools/process.py::ManagedProcess reads it, service_runtime only consumes the typed fact.

_NONCE = "n1"


class _ServiceBackend:
    """A scripted container backend with the production launcher contract (the launcher execs the service and writes its not-started record only
    when that exec fails). ``refuse_with``: the backend refuses before any launcher runs, printing exactly that text and exiting 125."""
    name = "scripted-oci"

    def __init__(self, refuse_with: str = ""):
        self.refuse_with, self.teardowns = refuse_with, 0

    def _teardown(self) -> None:
        self.teardowns += 1

    def prepare(self, profile, command):  # noqa: ARG002 - the Protocol's signature
        stage = FiniteLaunchStage(prefix=(sys.executable, "-c", _SIMULATED_LAUNCHER, _NONCE), marker=f"{TARGET_NOT_STARTED_MARKER}{_NONCE}",
                                  reached=f"{LAUNCHER_REACHED_MARKER}{_NONCE}")
        prefix = (sys.executable, "-c", _REFUSAL, self.refuse_with) if self.refuse_with else None
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage, command_prefix=prefix, cleanup=self._teardown)


def _verify_service(tmp_path: Path, backend: _ServiceBackend, command=None, *, script: str = "", port: int = 0):
    port = port or service_harness._free_port()
    spec = service_harness._spec(port, script or service_harness._http_service_script(), startup_timeout=10.0)
    spec = dataclasses.replace(spec, cwd=str(tmp_path), **({"service_command": list(command)} if command else {}))
    return service_runtime.run_managed_service_verification(spec, controller=ProcessController(), containment_profile=_profile(tmp_path), containment_backend=backend)


def _no_launch_record(*texts: str) -> bool:
    return not any(LAUNCHER_REACHED_MARKER in text or TARGET_NOT_STARTED_MARKER in text for text in texts)


@pytest.mark.parametrize("refusal", ["other", "bind_source_absent"])
def test_a_managed_launch_the_backend_refused_is_a_typed_environment_fact_never_a_service_verdict(tmp_path, refusal):
    """ORIGINAL SYMPTOM, the measured scenario: the backend refuses before the service's launcher runs."""
    text = OTHER_DOCKER_ERROR if refusal == "other" else _bind_source(tmp_path, present=False)
    code = BACKEND_TARGET_NOT_STARTED if refusal == "other" else BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST
    backend = _ServiceBackend(text)

    result = _verify_service(tmp_path, backend)

    kinds = service_runtime.ServiceVerificationOutcomeKind
    assert result.outcome is kinds.SERVICE_START_FAILED and result.outcome is not kinds.SERVICE_EXITED_BEFORE_READY
    assert result.passed is False and result.returncode is None, "the launcher's exit status (125) is not the service's and is not reported as one"
    assert result.environment_reason_code == code and code in ENVIRONMENT_NOT_STARTED_CODES
    assert result.to_dict()["environment_reason_code"] == code
    assert text.splitlines()[0] in result.stderr, "the backend's own text is kept as evidence"
    assert backend.teardowns == 1, "the containment teardown still runs exactly once"


def test_a_service_the_launcher_could_not_exec_never_started_and_its_launch_records_are_not_evidence(tmp_path):
    """The other never-started shape: the launcher ran and its exec of the service failed (its own not-started record)."""
    result = _verify_service(tmp_path, _ServiceBackend(), command=["kriya-no-such-service-binary-xyz", "--serve"])

    assert result.outcome is service_runtime.ServiceVerificationOutcomeKind.SERVICE_START_FAILED and result.returncode is None
    assert result.environment_reason_code == BACKEND_TARGET_NOT_STARTED
    assert _no_launch_record(result.stdout, result.stderr, result.reasoning), "a launcher record is a typed fact, never recorded service output"


def test_negative_control_a_service_that_started_and_then_crashed_is_still_exited_before_ready(tmp_path):
    """Same exit status as the backend's refusal (125): the status decides nothing. A service that RAN and exited is a statement about it."""
    crash = "import sys; sys.stderr.write('boom\\n'); sys.exit(125)"
    backend = _ServiceBackend()

    result = _verify_service(tmp_path, backend, command=[sys.executable, "-c", crash])

    assert result.outcome is service_runtime.ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY and result.returncode == 125
    assert result.environment_reason_code is None and "environment_reason_code" not in result.to_dict()
    assert result.stderr == "boom\n", "exactly the service's own stderr: the launcher's reached record is not in it"
    assert backend.teardowns == 1


def test_negative_control_a_healthy_managed_service_passes_unchanged_through_the_launch_stage(tmp_path):
    backend = _ServiceBackend()

    result = _verify_service(tmp_path, backend)

    assert result.outcome is service_runtime.ServiceVerificationOutcomeKind.PROBE_PASSED and result.passed is True and result.probe_status == 200
    assert result.environment_reason_code is None and "environment_reason_code" not in result.to_dict()
    assert _no_launch_record(result.stdout, result.stderr) and backend.teardowns == 1


def _exited(managed, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while managed.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return managed.poll()


def test_the_managed_process_owns_the_start_fact_and_a_noisy_service_cannot_push_it_out_of_the_bounded_buffer(tmp_path):
    """The fact is the process layer's. The ``reached`` record is the FIRST thing on stderr and the buffer keeps only a tail, so it is consumed as it
    arrives: a service that started and then wrote more than the buffer holds is still a started service."""
    controller = ProcessController(max_output_chars=200)
    noisy = "import sys\nfor _ in range(200):\n    sys.stderr.write('x' * 50 + '\\n')\nsys.exit(3)\n"
    managed = controller.start_managed([sys.executable, "-c", noisy], cwd=str(tmp_path), containment_profile=_profile(tmp_path), containment_backend=_ServiceBackend())
    try:
        assert _exited(managed) == 3
        assert managed.target_started() is True and managed.launch_refusal_diagnostic() is None
        stderr = managed.captured_output()[1]
        assert "x" * 50 in stderr and _no_launch_record(stderr)
    finally:
        managed.terminate(reap_timeout=5)

    running = controller.start_managed([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path), containment_profile=_profile(tmp_path),
                                       containment_backend=_ServiceBackend())
    try:
        assert running.target_started() is None, "nothing is decided about a process that is still running"
    finally:
        running.terminate(reap_timeout=5)

    # the production buffer size here: the diagnostic reads the backend's whole refusal text, which the 200-character buffer above would cut
    refused = ProcessController().start_managed([sys.executable, "-c", "pass"], cwd=str(tmp_path), containment_profile=_profile(tmp_path),
                                                containment_backend=_ServiceBackend(_bind_source(tmp_path, present=False)))
    try:
        assert _exited(refused) == 125 and refused.target_started() is False
        assert refused.launch_refusal_diagnostic()["reason_code"] == BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST
    finally:
        refused.terminate(reap_timeout=5)


def test_a_backend_without_a_launch_stage_reports_a_managed_process_started(tmp_path):
    """No stage, no fact: as for a finite ProcessResult, such a process is started whatever it exited with (the pre-stage behaviour, never a guess)."""
    class _NoStage:
        name = "scripted-oci"

        def prepare(self, profile, command):  # noqa: ARG002 - the Protocol's signature
            return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name)

    managed = ProcessController().start_managed([sys.executable, "-c", "import sys; sys.exit(125)"], cwd=str(tmp_path), containment_profile=_profile(tmp_path),
                                                containment_backend=_NoStage())
    try:
        assert _exited(managed) == 125 and managed.target_started() is True
    finally:
        managed.terminate(reap_timeout=5)


def _managed_refusal_stop(tmp_path: Path):
    """The stop the workflow raises for a refused managed launch (kriya/workflow/runtime_verification.py::_execute_managed_service_verification hands the
    tool result's typed code to this same function; tests/test_runtime_verification.py pins that hand-off), built from the REAL tool result."""
    workspace = tmp_path / "refused-service"
    workspace.mkdir()
    result = _verify_service(workspace, _ServiceBackend(OTHER_DOCKER_ERROR))
    with pytest.raises(QualityGateFailure) as stop:
        _stop_on_environment_gate_result(_State(), {"environment_reason_code": result.environment_reason_code, "output": result.stderr}, "managed_service")
    return stop.value


@pytest.mark.asyncio
async def test_a_refused_managed_service_launch_charges_nothing_and_neither_replaces_nor_opens_a_finding(tmp_path):
    stop = _managed_refusal_stop(tmp_path)
    assert stop.failure.type in ENVIRONMENT_VERDICT_FAILURE_TYPES and is_candidate_finding(stop.failure.type) is False
    assert stop.failure.diagnostics["reason_code"] == BACKEND_TARGET_NOT_STARTED and stop.failure.diagnostics["gate"] == "managed_service"

    run = await reset_harness._Run(tmp_path).play(reset_harness.RUN10_TO_ATTEMPT_5)
    before = run.rows[-1]
    budgets_before = dataclasses.replace(run.state.budgets, seen_failure_signatures=set(run.state.budgets.seen_failure_signatures))

    await run.play([(lambda: stop, None)])
    after = run.rows[-1]

    assert after.findings == before.findings, "the refusal never replaces the open finding"
    assert after.family == before.family and run.resets == [2], "... never opens a failure family or replenishes a scoped budget"
    recorded = dataclasses.replace(budgets_before, fallback_attempts_used=run.state.budgets.fallback_attempts_used)   # dispatch accounting only, as above
    assert run.state.budgets == recorded, "... and charges no budget"

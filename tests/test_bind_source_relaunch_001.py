"""BIND-SOURCE-RELAUNCH-001 (owner ruling 2026-11-11; root DOCKER-BIND-SOURCE-REFUSED-AFTER-PATHNAME-RECREATION-001).

MEASURED (pre-live gate on b64bd4b, then 1 of 24 crash subprocesses under 8 workers): the container backend refused to start a
gate's container with `bind source path does not exist: <path>` while the path existed on the host. TRACED + reproduced at the
launch seam: the Java adapters already turn a never-started target into a typed environment stop, but the Python compile gate
returned an ordinary failure with no ``environment_reason_code`` - reported as COMPILATION FAILURE and handed to the Developer.

Contract: for EXACTLY the typed condition ``BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST`` (established by
kriya/tools/containment.py from a target that never started), the validator's launch seam makes the SAME launch once more -
same command, cwd, stdin, profile and backend, bounded by what is left of the launch's own timeout. Both launches stay in
evidence. A second identical refusal is the typed ``verification_infrastructure_failure`` stop; never a candidate failure and
never a Developer repair. A source absent on the host, any other backend error and every started target are untouched.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_bind_source_diagnostic_001 import _LAUNCHER, _REFUSAL, _profile, _stderr

from kriya.tools import validate as validate_module
from kriya.tools.containment import (
    BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST,
    LAUNCHER_REACHED_MARKER,
    TARGET_NOT_STARTED_MARKER,
    FiniteLaunchStage,
    PreparedContainment,
)
from kriya.tools.containment import BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST as PRESENT
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.gate_orchestrator import QualityGateFailure, _stop_on_environment_gate_result

GOOD = "def add(a, b):\n    return a + b\n"
BROKEN = "def add(a, b)\n    return a + b\n"
OTHER_DOCKER_ERROR = "docker: Error response from daemon: Minimum memory limit allowed is 6MB.\nSee 'docker run --help'."


class _ScriptedBackend:
    """A container backend that refuses to start the container with ``refusals[i]`` on its i-th launch (the exact text docker
    prints) and runs the target through the real launcher contract once the script is exhausted."""
    name = "scripted-oci"

    def __init__(self, *refusals: str):
        self.refusals, self.prepared = list(refusals), []

    def prepare(self, profile, command):
        self.prepared.append((profile, list(command)))
        stage = FiniteLaunchStage(prefix=(sys.executable, "-c", _LAUNCHER, "n1"), marker=f"{TARGET_NOT_STARTED_MARKER}n1", reached=f"{LAUNCHER_REACHED_MARKER}n1")
        if len(self.prepared) <= len(self.refusals):
            return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage,
                                       command_prefix=(sys.executable, "-c", _REFUSAL, self.refusals[len(self.prepared) - 1]))
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=stage)


def _workspace(tmp_path: Path, source: str = GOOD) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "calc.py").write_text(source, encoding="utf-8")
    return workspace


def _validator(workspace: Path, monkeypatch, backend: _ScriptedBackend) -> PolymorphicValidator:
    validator = PolymorphicValidator(str(workspace))
    profile = _profile(workspace)
    monkeypatch.setattr(validator, "build_containment_profile_and_backend", lambda **_kwargs: (profile, backend))
    return validator


def _bind_source(tmp_path: Path, *, present: bool = True) -> str:
    source = tmp_path / "state" / "gate-views" / "view-0123456789abcdef0000061d000001"
    if present:
        source.mkdir(parents=True)
    return _stderr(str(source))


def _digest(workspace: Path) -> str:
    return hashlib.sha256(b"".join(path.read_bytes() for path in sorted(workspace.rglob("*.py")))).hexdigest()


class _State:
    """What ``_stop_on_environment_gate_result`` touches of the run state."""
    attempt_number = 1

    def __init__(self):
        self.outcomes = []

    def record_gate_outcome(self, outcome):
        self.outcomes.append(outcome)


# ---- the old misclassification, and its replacement ---------------------------------------------------------------------------------

def test_the_same_refusal_twice_is_the_typed_infrastructure_stop_after_exactly_one_relaunch(tmp_path, monkeypatch):
    """The reproducer: the exact docker text at the launch seam, the bind source present on the host. Before the fix this was ONE
    launch and an ordinary compile failure with no environment code (MEASURED: prefix_probe_compile.json)."""
    workspace, refusal = _workspace(tmp_path), _bind_source(tmp_path)
    backend = _ScriptedBackend(refusal, refusal, refusal)
    validator = _validator(workspace, monkeypatch, backend)
    result = validator.run_compile_check(["calc.py"])
    assert len(backend.prepared) == 2, "exactly one relaunch, never a second"
    assert result["success"] is False and result["environment_reason_code"] == PRESENT
    assert result["output"].startswith(f"{PRESENT}: ") and "bind source path does not exist" in result["output"]
    recovery = result["infrastructure_recovery"]
    assert (recovery["reason_code"], recovery["relaunches"], recovery["relaunch_outcome"]) == (PRESENT, 1, "SAME_CONDITION")
    assert recovery["first_launch"]["reason_code"] == PRESENT and recovery["first_launch"]["host_source_facts"]["exists"] is True
    assert result["infrastructure_diagnostic"]["reason_code"] == PRESENT                       # the relaunch's own refusal
    # the gate consumer: the existing environment stop, never a compile failure offered for repair
    state = _State()
    with pytest.raises(QualityGateFailure) as stopped:
        _stop_on_environment_gate_result(state, result, "compile")
    failure = stopped.value.failure
    assert failure.type == "verification_infrastructure_failure" and failure.diagnostics["reason_code"] == PRESENT
    assert "not a code failure" in failure.message and len(state.outcomes) == 1


def test_one_refusal_then_a_started_target_is_judged_by_its_own_output(tmp_path, monkeypatch):
    workspace, refusal = _workspace(tmp_path), _bind_source(tmp_path)
    backend = _ScriptedBackend(refusal)
    validator = _validator(workspace, monkeypatch, backend)
    before = _digest(workspace)
    result = validator.run_compile_check(["calc.py"])
    assert len(backend.prepared) == 2 and result["success"] is True and "environment_reason_code" not in result
    assert "infrastructure_diagnostic" not in result                                         # the relaunch started: no refusal on the verdict's launch
    recovery = result["infrastructure_recovery"]                                             # ... and the first launch's refusal is still in evidence
    assert (recovery["relaunch_outcome"], recovery["relaunch_returncode"], recovery["first_launch_returncode"]) == ("TARGET_STARTED", 0, 125)
    assert recovery["first_launch"]["reason_code"] == PRESENT and "captured_at" in recovery["first_launch"]
    assert _digest(workspace) == before, "the candidate's bytes changed across the relaunch"
    _stop_on_environment_gate_result(_State(), result, "compile")                            # no stop: the gate goes on normally


def test_a_recovered_launch_still_convicts_a_real_compile_error(tmp_path, monkeypatch):
    workspace, refusal = _workspace(tmp_path, BROKEN), _bind_source(tmp_path)
    backend = _ScriptedBackend(refusal)
    result = _validator(workspace, monkeypatch, backend).run_compile_check(["calc.py"])
    assert len(backend.prepared) == 2 and result["success"] is False and "environment_reason_code" not in result
    assert "SyntaxError" in result["output"] and result["infrastructure_recovery"]["relaunch_outcome"] == "TARGET_STARTED"


# ---- what is NOT relaunched ----------------------------------------------------------------------------------------------------------

def test_an_ordinary_compile_error_is_a_candidate_compile_failure_as_before(tmp_path, monkeypatch):
    backend = _ScriptedBackend()
    result = _validator(_workspace(tmp_path, BROKEN), monkeypatch, backend).run_compile_check(["calc.py"])
    assert len(backend.prepared) == 1 and result["success"] is False and "SyntaxError" in result["output"]
    assert "environment_reason_code" not in result and "infrastructure_recovery" not in result and "infrastructure_diagnostic" not in result
    _stop_on_environment_gate_result(_State(), result, "compile")                            # not an environment stop


def test_an_ordinary_test_failure_at_the_launch_seam_is_untouched(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    backend = _ScriptedBackend()
    validator = _validator(workspace, monkeypatch, backend)
    failing = [sys.executable, "-c", "import sys; print('1 failed, 2 passed'); sys.exit(1)"]
    launched = validator._run_cmd_with_timeout(failing, cwd=str(workspace))
    result = validator._validation_result(launched["returncode"] in (0, 5), launched["stdout"] + "\n" + launched["stderr"], launched)
    assert len(backend.prepared) == 1 and launched["returncode"] == 1 and "target_started" not in launched
    assert result["success"] is False and "1 failed" in result["output"]
    assert "environment_reason_code" not in result and "infrastructure_recovery" not in result


def test_a_bind_source_that_is_absent_on_the_host_is_never_relaunched(tmp_path, monkeypatch):
    backend = _ScriptedBackend(_bind_source(tmp_path, present=False))
    result = _validator(_workspace(tmp_path), monkeypatch, backend).run_compile_check(["calc.py"])
    assert len(backend.prepared) == 1, "a source that does not exist on the host is not a transient backend view"
    assert result["infrastructure_diagnostic"]["reason_code"] == BACKEND_BIND_SOURCE_MISSING_AND_ABSENT_ON_HOST
    assert "infrastructure_recovery" not in result and result.get("environment_reason_code") != PRESENT


def test_an_unrelated_backend_error_is_never_relaunched(tmp_path, monkeypatch):
    backend = _ScriptedBackend(OTHER_DOCKER_ERROR)
    result = _validator(_workspace(tmp_path), monkeypatch, backend).run_compile_check(["calc.py"])
    assert len(backend.prepared) == 1 and result["success"] is False
    assert "infrastructure_recovery" not in result and "infrastructure_diagnostic" not in result and result.get("environment_reason_code") != PRESENT


def test_a_started_target_that_prints_the_signature_is_never_relaunched(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    backend = _ScriptedBackend()
    validator = _validator(workspace, monkeypatch, backend)
    forged = [sys.executable, "-c", "import sys; sys.stderr.write(sys.argv[1]); sys.exit(1)", _bind_source(tmp_path)]
    launched = validator._run_cmd_with_timeout(forged, cwd=str(workspace))
    assert len(backend.prepared) == 1 and "infrastructure_recovery" not in launched and "infrastructure_diagnostic" not in launched
    assert "environment_reason_code" not in validator._validation_result(False, launched["stderr"], launched)


# ---- the bound: one relaunch per verification scope, the same launch, no clock reset ----------------------------------------------------

def test_one_verification_scope_has_one_relaunch_and_the_next_scope_has_its_own(tmp_path, monkeypatch):
    workspace, refusal = _workspace(tmp_path), _bind_source(tmp_path)
    backend = _ScriptedBackend(*[refusal] * 10)
    validator = _validator(workspace, monkeypatch, backend)
    command = [sys.executable, "-c", "print('ok')"]
    with validator.dependency_cache_scope():
        first = validator._run_cmd_with_timeout(command, cwd=str(workspace))
        second = validator._run_cmd_with_timeout(command, cwd=str(workspace))
        assert len(backend.prepared) == 3, "the scope's single relaunch was already used by its first launch"
        assert first["infrastructure_recovery"]["relaunch_outcome"] == "SAME_CONDITION" and "infrastructure_recovery" not in second
        assert second["infrastructure_diagnostic"]["reason_code"] == PRESENT and second["target_started"] is False
    with validator.dependency_cache_scope():
        validator._run_cmd_with_timeout(command, cwd=str(workspace))
    assert len(backend.prepared) == 5, "a new verification scope starts with its own single relaunch"


def test_the_relaunch_is_the_same_launch_bounded_by_what_is_left_of_its_timeout(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    source = tmp_path / "view-x"
    source.mkdir()
    diagnostic = validate_module.BACKEND_BIND_SOURCE_MISSING_BUT_PRESENT_ON_HOST
    refused = SimpleNamespace(returncode=125, target_started=False,
                              infrastructure_diagnostic={"reason_code": diagnostic, "reported_source": str(source), "host_source": str(source), "meaning": "m", "backend": "oci"},
                              to_dict=lambda: {"returncode": 125, "stdout": "", "stderr": "refused", "timeout": False, "target_started": False})
    started = SimpleNamespace(returncode=0, target_started=True, infrastructure_diagnostic=None,
                              to_dict=lambda: {"returncode": 0, "stdout": "ok", "stderr": "", "timeout": False})
    script, launches = [refused, started], []

    class _Controller:
        def run(self, cmd, **kwargs):
            launches.append((list(cmd), dict(kwargs)))
            return script[len(launches) - 1]

    clock = iter([1000.0, 1040.5])                         # the first launch took 40.5 s of its 300 s
    real_monotonic = validate_module.time.monotonic
    monkeypatch.setattr(validate_module, "ProcessController", _Controller)
    monkeypatch.setattr(validate_module.time, "monotonic", lambda: next(clock, None) or real_monotonic())
    backend = _ScriptedBackend()
    validator = _validator(workspace, monkeypatch, backend)
    command = ["python3", "-m", "py_compile", "calc.py"]
    # no wait is added between the two launches (guarded here only: this call opens no verification scope, whose own cleanup may sleep)
    with monkeypatch.context() as guarded:
        guarded.setattr(validate_module.time, "sleep", lambda *_a, **_k: pytest.fail("the launch seam must not wait for the backend"))
        result = validator._run_cmd_with_timeout(command, cwd=str(workspace), timeout=300, stdin_payload="payload")
    assert len(launches) == 2 and result["returncode"] == 0
    (first_cmd, first), (second_cmd, second) = launches
    assert first_cmd == second_cmd == command
    for key in ("cwd", "stdin_payload"):
        assert first[key] == second[key]
    assert first["containment_profile"] is second["containment_profile"] and first["containment_backend"] is second["containment_backend"] is backend
    assert (first["timeout"], second["timeout"]) == (300, 259), "the relaunch is bounded by what is left: nothing is reset or extended"
    assert result["infrastructure_recovery"]["relaunch_timeout_seconds"] == 259


def test_a_launch_that_used_its_whole_timeout_leaves_the_relaunch_the_one_second_floor(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    refused = SimpleNamespace(returncode=125, target_started=False, infrastructure_diagnostic={"reason_code": PRESENT},
                              to_dict=lambda: {"returncode": 125, "stdout": "", "stderr": "refused", "timeout": False, "target_started": False})
    timeouts = []

    class _Controller:
        def run(self, cmd, **kwargs):
            timeouts.append(kwargs["timeout"])
            return refused

    clock = iter([50.0, 500.0])
    real_monotonic = validate_module.time.monotonic
    monkeypatch.setattr(validate_module, "ProcessController", _Controller)
    monkeypatch.setattr(validate_module.time, "monotonic", lambda: next(clock, None) or real_monotonic())
    validator = _validator(workspace, monkeypatch, _ScriptedBackend())
    validator._run_cmd_with_timeout(["python3", "-V"], cwd=str(workspace), timeout=30)
    assert timeouts == [30, 1]


# ---- evidence: both launches reach the gate's own record ----------------------------------------------------------------------------------

def test_the_gate_result_record_carries_the_first_refusal_and_the_relaunch(tmp_path, monkeypatch):
    workspace, refusal = _workspace(tmp_path), _bind_source(tmp_path)
    backend = _ScriptedBackend(refusal)
    validator = _validator(workspace, monkeypatch, backend)
    recorded = []
    monkeypatch.setattr(validate_module.attempt_evidence_scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(validate_module.attempt_evidence_scope, "emit", lambda kind, payload, **_k: recorded.append((kind, dict(payload))))
    assert validator.run_compile_check(["calc.py"])["success"] is True
    ((kind, payload),) = recorded
    assert kind == "gate.result" and payload["gate"] == "compile" and payload["success"] is True
    assert payload["infrastructure_recovery"]["first_launch"]["reason_code"] == PRESENT
    assert payload["infrastructure_recovery"]["relaunch_outcome"] == "TARGET_STARTED"


def test_a_gate_without_the_condition_records_no_recovery(tmp_path, monkeypatch):
    validator = _validator(_workspace(tmp_path), monkeypatch, _ScriptedBackend())
    recorded = []
    monkeypatch.setattr(validate_module.attempt_evidence_scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(validate_module.attempt_evidence_scope, "emit", lambda kind, payload, **_k: recorded.append((kind, dict(payload))))
    assert validator.run_compile_check(["calc.py"])["success"] is True
    assert [kind for kind, _payload in recorded] == ["gate.result"] and "infrastructure_recovery" not in recorded[0][1]


def test_the_typed_stop_needs_the_never_started_fact_not_only_a_diagnostic(tmp_path):
    """The containment owner attaches the diagnostic only to a target that never started; the validator still requires that
    fact itself, so a result that merely carries a diagnostic (built elsewhere, or a started target) is never turned into an
    environment stop."""
    source = tmp_path / "view-x"
    source.mkdir()
    diagnostic = {"reason_code": PRESENT, "meaning": "m", "reported_source": str(source), "backend": "oci"}
    started = {"returncode": 1, "stdout": "", "stderr": "SyntaxError", "infrastructure_diagnostic": diagnostic}
    assert "environment_reason_code" not in PolymorphicValidator._validation_result(False, "SyntaxError", started)
    never_started = {**started, "target_started": False}
    assert PolymorphicValidator._validation_result(False, "refused", never_started)["environment_reason_code"] == PRESENT
    already_typed = {**never_started, "environment_reason_code": "MAVEN_INVOCATION_FAILED"}
    assert PolymorphicValidator._validation_result(False, "refused", already_typed)["environment_reason_code"] == "MAVEN_INVOCATION_FAILED"

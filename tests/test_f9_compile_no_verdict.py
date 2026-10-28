"""F9 (Wave-2; owner decision D6): no compile path returns PASS unless a trustworthy applicable compiler actually established it.

MEASURED at abc2d67 (reproducer: ``WAVE2_STEP0_2026-10-28_abc2d67/F9/f9_repro.py``): a Maven ``PermissionError`` / ``OSError(ENOEXEC)`` / ``RuntimeError`` / a
malformed Maven result, with ``pom.xml`` the only changed file, ended the overall compile gate in PASS ("No Java files to compile."): ``MavenBuildAdapter.compile``
swallowed the exception into ``None`` ("did not decide"), the chain continued, and ``JAVAC.compile`` returned its no-source success. Gradle had the same shape.

The compile result contract (``kriya/capabilities/ports.py``): PASS / FAIL (the compiler ran and rejected the code) / typed no-verdict (``success`` False with an
``environment_reason_code``) / ``None`` ONLY for "not applicable". These tests drive the REAL adapter chain (``run_compile_check``) with the documented patch
points (``_run_maven_cmd``, ``_run_gradle_cmd``, ``_run_cmd_with_timeout`` for javac) and a javac sentinel that fails the test if the downstream fallback is consulted
after an applicable build system already answered, which is exactly the wash-out the defect needed."""
import errno
import logging
import os
from unittest.mock import patch

import pytest

from kriya.capabilities import GRADLE, MAVEN
from kriya.config import AppConfig
from kriya.tools.containment import ContainmentSetupError
from kriya.tools.dependency_execution import (
    GRADLE_COMPILE_INDETERMINATE,
    GRADLE_INVOCATION_FAILED,
    MAVEN_COMPILE_INDETERMINATE,
    MAVEN_INVOCATION_FAILED,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
)
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.gate_orchestrator import _stop_on_environment_gate_result  # pylint: disable=protected-access

POM = "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version></project>"
BROKEN_JAVA = "class App { int x = \"not an int\"; }"


def _workspace(root, *, pom=False, gradle=False, java=True):
    root.mkdir(parents=True, exist_ok=True)
    if pom:
        (root / "pom.xml").write_text(POM)
    if gradle:
        (root / "build.gradle").write_text("plugins { id 'java' }\n")
    if java:
        source = root / "src" / "main" / "java" / "App.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(BROKEN_JAVA)
    return PolymorphicValidator(str(root), autonomy_cfg=AppConfig().autonomy)


class _JavacSentinel:
    """Stands in for the javac fallback's process seam (``_run_cmd_with_timeout``); records every consultation. ``seam`` is a plain function so it binds
    as a method when patched onto the class - a callable instance would not receive ``self`` and the sentinel would silently never record."""

    def __init__(self, result=None):
        self.calls = []
        self._result = result or {"returncode": 1, "stdout": "", "stderr": "App.java:1: error: incompatible types"}

        def seam(_validator, cmd, cwd=None, **_kwargs):
            self.calls.append(list(cmd))
            return dict(self._result)

        self.seam = seam


_PERMISSION = PermissionError(errno.EACCES, "Permission denied", "mvn")
_ENOEXEC = OSError(errno.ENOEXEC, "Exec format error", "mvn")

# (label, injected exception or None, injected return value, expected typed code)
_MAVEN_NO_VERDICT = [
    ("PermissionError", _PERMISSION, None, MAVEN_INVOCATION_FAILED),
    ("OSError(ENOEXEC)", _ENOEXEC, None, MAVEN_INVOCATION_FAILED),
    ("FileNotFoundError", FileNotFoundError("mvn"), None, MAVEN_INVOCATION_FAILED),
    ("RuntimeError", RuntimeError("boom"), None, MAVEN_COMPILE_INDETERMINATE),
    ("ValueError", ValueError("bad"), None, MAVEN_COMPILE_INDETERMINATE),
    ("empty result", None, {}, MAVEN_COMPILE_INDETERMINATE),
    ("returncode None", None, {"returncode": None, "stdout": "", "stderr": ""}, MAVEN_COMPILE_INDETERMINATE),
    ("returncode str", None, {"returncode": "0", "stdout": "", "stderr": ""}, MAVEN_COMPILE_INDETERMINATE),
    ("not a dict", None, "garbage", MAVEN_COMPILE_INDETERMINATE),
    ("contained mvn never started", None, {"returncode": 127, "stdout": "", "stderr": "no such file", "target_started": False}, MAVEN_INVOCATION_FAILED),
]
_GRADLE_NO_VERDICT = [
    ("PermissionError", PermissionError(errno.EACCES, "Permission denied", "gradle"), None, GRADLE_INVOCATION_FAILED),
    ("OSError(ENOEXEC)", OSError(errno.ENOEXEC, "Exec format error", "gradle"), None, GRADLE_INVOCATION_FAILED),
    ("FileNotFoundError", FileNotFoundError("gradle"), None, GRADLE_INVOCATION_FAILED),
    ("RuntimeError", RuntimeError("boom"), None, GRADLE_COMPILE_INDETERMINATE),
    ("empty result", None, {}, GRADLE_COMPILE_INDETERMINATE),
    ("returncode None", None, {"returncode": None, "stdout": "", "stderr": ""}, GRADLE_COMPILE_INDETERMINATE),
    ("not a dict", None, "garbage", GRADLE_COMPILE_INDETERMINATE),
]


def _inject(exception, value):
    return {"side_effect": exception} if exception is not None else {"return_value": value}


# --- Maven: every no-verdict shape, with and without a changed .java, never reaches javac -------------------------------------------
@pytest.mark.parametrize("changed", [["pom.xml"], ["src/main/java/App.java"]], ids=["pom-only", "broken-java-changed"])
@pytest.mark.parametrize("label,exception,value,code", _MAVEN_NO_VERDICT, ids=[row[0] for row in _MAVEN_NO_VERDICT])
def test_a_maven_without_a_verdict_is_a_typed_failure_and_javac_is_never_consulted(tmp_path, changed, label, exception, value, code):
    validator = _workspace(tmp_path, pom=True)
    sentinel = _JavacSentinel()
    with patch.object(PolymorphicValidator, "_run_maven_cmd", **_inject(exception, value)), \
            patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=sentinel.seam):
        result = validator.run_compile_check(changed)
    assert result["success"] is False, f"{label}: a compile with no trustworthy verdict must never be PASS: {result}"
    assert result["environment_reason_code"] == code, f"{label}: {result}"
    assert sentinel.calls == [], f"{label}: the downstream javac fallback was consulted after Maven applied: {sentinel.calls}"
    assert "No Java files to compile" not in result["output"]


@pytest.mark.parametrize("changed", [["build.gradle"], ["src/main/java/App.java"]], ids=["build-script-only", "broken-java-changed"])
@pytest.mark.parametrize("label,exception,value,code", _GRADLE_NO_VERDICT, ids=[row[0] for row in _GRADLE_NO_VERDICT])
def test_a_gradle_without_a_verdict_is_a_typed_failure_and_javac_is_never_consulted(tmp_path, changed, label, exception, value, code):
    validator = _workspace(tmp_path, gradle=True)
    sentinel = _JavacSentinel()
    with patch.object(PolymorphicValidator, "_run_gradle_cmd", **_inject(exception, value)), \
            patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=sentinel.seam):
        result = validator.run_compile_check(changed)
    assert result["success"] is False, f"{label}: {result}"
    assert result["environment_reason_code"] == code, f"{label}: {result}"
    assert sentinel.calls == [], f"{label}: the downstream javac fallback was consulted after Gradle applied: {sentinel.calls}"
    assert "No Java files to compile" not in result["output"]


# --- the supported behaviours stay exactly right (controls: the tests above must not pass by making everything fail) ---------------------
def test_a_genuine_maven_compiler_failure_is_a_candidate_failure_not_an_environment_outcome(tmp_path):
    validator = _workspace(tmp_path, pom=True)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value={
            "returncode": 1, "stderr": "",
            # Slice F: the conviction needs a LOCATED diagnostic; the bare summary this control used to carry is a typed no-verdict now (tests/test_slice_f_compile_classifier.py)
            "stdout": "[ERROR] COMPILATION ERROR\n[ERROR] /ws/src/main/java/App.java:[1,24] incompatible types"}):
        result = validator.run_compile_check(["src/main/java/App.java"])
    assert result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Maven compilation failed:")


def test_a_genuine_gradle_compiler_failure_is_a_candidate_failure_not_an_environment_outcome(tmp_path):
    validator = _workspace(tmp_path, gradle=True)
    with patch.object(PolymorphicValidator, "_run_gradle_cmd", return_value={
            "returncode": 1, "stdout": "/ws/src/main/java/App.java:1: error: incompatible types", "stderr": "FAILED"}):   # Slice F: a LOCATED javac diagnostic
        result = validator.run_compile_check(["src/main/java/App.java"])
    assert result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Gradle compilation failed:")


def test_a_maven_that_ran_and_exited_zero_is_still_a_pass(tmp_path):
    validator = _workspace(tmp_path, pom=True, java=False)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value={"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": ""}):
        result = validator.run_compile_check(["pom.xml"])
    assert result["success"] is True and result["output"] == "Maven compilation succeeded."


def test_a_gradle_that_ran_and_exited_zero_is_still_a_pass(tmp_path):
    validator = _workspace(tmp_path, gradle=True, java=False)
    with patch.object(PolymorphicValidator, "_run_gradle_cmd", return_value={"returncode": 0, "stdout": "BUILD SUCCESSFUL", "stderr": ""}):
        result = validator.run_compile_check(["build.gradle"])
    assert result["success"] is True and result["output"] == "Gradle compilation succeeded."


def test_a_typed_gradle_environment_outcome_from_the_validator_keeps_its_own_code(tmp_path):
    """The validator's own typed Gradle outcome (distribution unavailable / launcher never started) passes through untouched, as before."""
    validator = _workspace(tmp_path, gradle=True)
    typed = {"returncode": 1, "stdout": "", "stderr": "wrapper", "environment_reason_code": "GRADLE_DISTRIBUTION_UNAVAILABLE"}
    with patch.object(PolymorphicValidator, "_run_gradle_cmd", return_value=typed):
        result = validator.run_compile_check(["build.gradle"])
    assert result["success"] is False and result["environment_reason_code"] == "GRADLE_DISTRIBUTION_UNAVAILABLE"


def test_a_containment_setup_failure_stays_fail_closed(tmp_path):
    for builder, seam, kwargs in ((MAVEN, "_run_maven_cmd", dict(pom=True)), (GRADLE, "_run_gradle_cmd", dict(gradle=True))):
        validator = _workspace(tmp_path / builder.build_system, **kwargs)
        with patch.object(PolymorphicValidator, seam, side_effect=ContainmentSetupError("daemon unreachable")):
            with pytest.raises(ContainmentSetupError):
                validator.run_compile_check(["src/main/java/App.java"])


def test_not_applicable_is_still_none_and_the_javac_no_source_success_still_applies_only_then(tmp_path):
    """None means ONLY "not applicable": no pom.xml / no build script. With no applicable build system and no changed .java the javac fallback's
    "No Java files to compile." success is the supported behaviour (a workspace that has Java sources but nothing to compile among the changes)."""
    bare = _workspace(tmp_path / "bare")
    assert MAVEN.compile(bare, ["src/main/java/App.java"], deadline=None) is None
    assert GRADLE.compile(bare, ["src/main/java/App.java"], deadline=None) is None
    sentinel = _JavacSentinel()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=sentinel.seam):
        result = bare.run_compile_check(["notes.txt"])
    assert result == {"success": True, "output": "No Java files to compile."} and sentinel.calls == []


def test_a_broken_changed_source_with_no_build_system_is_still_convicted_by_javac(tmp_path):
    bare = _workspace(tmp_path / "bare")
    sentinel = _JavacSentinel()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=sentinel.seam):
        result = bare.run_compile_check(["src/main/java/App.java"])
    assert result["success"] is False and "environment_reason_code" not in result and len(sentinel.calls) == 1


# --- the gates attribute it correctly, and a coding error stays visible ------------------------------------------------------------------
class _GateState:
    attempt_number = 1

    def __init__(self):
        self.recorded = []

    def record_gate_outcome(self, outcome):
        self.recorded.append(outcome)


@pytest.mark.parametrize("code,phrase", [
    (MAVEN_INVOCATION_FAILED, "could not start"), (GRADLE_INVOCATION_FAILED, "could not start"),
    (MAVEN_COMPILE_INDETERMINATE, "returned no trustworthy verdict"), (GRADLE_COMPILE_INDETERMINATE, "returned no trustworthy verdict")])
def test_the_compile_gate_stops_a_no_verdict_as_verification_infrastructure_not_as_a_code_failure(code, phrase):
    state = _GateState()
    with pytest.raises(QualityGateFailure) as raised:
        _stop_on_environment_gate_result(state, {"success": False, "environment_reason_code": code, "output": "Failed to invoke mvn compile: x"}, "compile")
    failure = raised.value.failure
    assert failure.type == "verification_infrastructure_failure" and failure.diagnostics["reason_code"] == code
    assert phrase in failure.message and "not a code failure" in failure.message and "COMPILATION FAILURE" not in failure.message
    assert not failure.failed_content and not failure.likely_files and not failure.file_locations   # nothing offered to the Developer to repair
    assert [outcome["type"] for outcome in state.recorded] == ["verification_infrastructure_failure"]


def test_the_no_verdict_vocabulary_words_an_indeterminate_adapter_as_no_verdict_and_an_unstartable_tool_as_could_not_start():
    assert {MAVEN_COMPILE_INDETERMINATE, GRADLE_COMPILE_INDETERMINATE} <= MAVEN_VALIDATE_NO_VERDICT_CODES
    assert not ({MAVEN_INVOCATION_FAILED, GRADLE_INVOCATION_FAILED} & MAVEN_VALIDATE_NO_VERDICT_CODES)


@pytest.mark.parametrize("adapter,seam,kwargs", [(MAVEN, "_run_maven_cmd", dict(pom=True)), (GRADLE, "_run_gradle_cmd", dict(gradle=True))],
                         ids=["maven", "gradle"])
def test_an_unexpected_exception_is_logged_with_its_traceback_not_swallowed(tmp_path, caplog, adapter, seam, kwargs):
    """Project rule 4: a broad catch must not turn a coding error into a quiet warning. The traceback is on the log record."""
    validator = _workspace(tmp_path, **kwargs)
    with patch.object(PolymorphicValidator, seam, side_effect=KeyError("returncode")):
        with caplog.at_level(logging.ERROR):
            result = adapter.compile(validator, ["src/main/java/App.java"], deadline=None)
    assert result["success"] is False and "KeyError" in result["output"]
    records = [r for r in caplog.records if r.levelno >= logging.ERROR and "no trustworthy verdict" in r.getMessage()]
    assert records and records[0].exc_info and records[0].exc_info[0] is KeyError


def test_the_dependency_regression_check_is_unchanged(tmp_path):
    original, candidate = tmp_path / "orig", tmp_path / "cand"
    for root, deps in ((original, "<dependency><groupId>g</groupId><artifactId>gone</artifactId><version>1</version></dependency>"), (candidate, "")):
        root.mkdir()
        (root / "pom.xml").write_text(
            "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version>"
            f"<dependencies>{deps}</dependencies></project>")
    validator = PolymorphicValidator(str(candidate), original_workspace_path=str(original), autonomy_cfg=AppConfig().autonomy)
    result = MAVEN.compile(validator, ["pom.xml"], deadline=None)
    assert result["success"] is False and "Dependency regression" in result["output"] and "environment_reason_code" not in result
    assert os.path.exists(candidate / "pom.xml")

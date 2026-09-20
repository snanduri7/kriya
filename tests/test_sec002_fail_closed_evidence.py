"""SEC-002 (2026-09-12, FAST implementation): deterministic, no-Docker,
no-live-model regression coverage for the containment-setup-failure
propagation fix in `kriya/tools/validate.py`.

Root cause (found during the prior DEEP DESIGN_INVESTIGATION, fixed here):
`run_compile_check`, `run_pom_validate`, `run_tests`, and `run_app` each had
at least one bare `except Exception as e:` sitting directly around a
contained `_run_maven_cmd`/`_run_cmd_with_timeout` call, with no special
case for `kriya.tools.containment.ContainmentSetupError` (and its
subclasses - `BackendUnavailableError`, `ResourceLimitSetupError`,
`RegistryAcquisitionSetupError`). The worst instance: `run_compile_check`'s
Java Maven block let a swallowed `ContainmentSetupError` fall through to
the Gradle check, then the raw `javac` fallback - when `files` contained
no `.java` entries, this reached `if not java_files: return
{"success": True, ...}`, reporting a genuine containment-setup failure as
gate PASS. `run_pom_validate` had an even more direct version: its
catch-all unconditionally returned `success: True` for ANY exception.

Fix (kriya/tools/validate.py): an `except ContainmentSetupError: raise`
clause was added immediately before every one of these generic handlers -
the same, already-established pattern this file already used for Maven
acquisition (`_acquire_for_this_goal`, SEC-006). This is a re-raise, not a
new classifier - the exception still reaches
`kriya.workflow.retry_strategy.handle_attempt_failure`'s existing
`isinstance(e, ContainmentSetupError)` check (already covered by
`tests/test_workflow.py::test_handle_attempt_failure_classifies_containment_setup_error_deterministically`),
unchanged. `run_app_sequence` was deliberately NOT changed to re-raise -
its existing swallow-into-a-dict-shape behavior is already correctly
recognized as an infrastructure failure by
`kriya.workflow.acceptance.runtime_verification_infrastructure_reason`
(never a false pass, never fed to the Developer as a code defect) and
changing it would touch callers outside this fix's scope.
"""
import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import BackendUnavailableError, ContainmentSetupError
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import runtime_verification_infrastructure_reason


def _contained_validator(tmp_path) -> PolymorphicValidator:
    (tmp_path / "pom.xml").write_text("<project></project>")
    return PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )


def _boom(*_args, **_kwargs):
    raise BackendUnavailableError("simulated: docker daemon unreachable")


# --- 1/2: run_compile_check, Java, Maven containment-setup failure ---

def test_maven_containment_failure_with_no_java_files_cannot_pass(tmp_path):
    """The most severe reproduction: previously this reached
    {"success": True, "output": "No Java files to compile."} - it must
    now raise instead."""
    validator = _contained_validator(tmp_path)
    validator._run_maven_cmd = _boom
    validator._run_cmd_with_timeout = _boom
    with pytest.raises(ContainmentSetupError):
        validator.run_compile_check(["pom.xml"])


def test_maven_containment_failure_with_java_files_cannot_fall_through_to_javac(tmp_path):
    """With a real .java file in scope, the failure must propagate
    directly out of the Maven block - it must never reach the javac
    fallback at all (previously it did, and happened to fail safely
    there only because javac's own call hit the same backend)."""
    validator = _contained_validator(tmp_path)
    (tmp_path / "Foo.java").write_text("public class Foo { public static void main(String[] a) {} }")
    validator._run_maven_cmd = _boom
    javac_called = []
    original = validator._run_cmd_with_timeout

    def _tracking_boom(cmd, *args, **kwargs):
        if cmd and cmd[0] == "javac":
            javac_called.append(cmd)
        return _boom()

    validator._run_cmd_with_timeout = _tracking_boom
    with pytest.raises(ContainmentSetupError):
        validator.run_compile_check(["Foo.java"])
    assert not javac_called, "javac fallback must never be reached after a Maven containment-setup failure"


# --- 3: run_compile_check, Java, Gradle containment-setup failure ---

def test_gradle_containment_failure_cannot_fall_through_to_javac(tmp_path):
    (tmp_path / "build.gradle").write_text("")
    (tmp_path / "Foo.java").write_text("public class Foo { public static void main(String[] a) {} }")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    javac_called = []

    def _tracking_boom(cmd, *args, **kwargs):
        if cmd and cmd[0] == "javac":
            javac_called.append(cmd)
        raise BackendUnavailableError("simulated: docker daemon unreachable")

    validator._run_cmd_with_timeout = _tracking_boom
    with pytest.raises(ContainmentSetupError):
        validator.run_compile_check(["Foo.java"])
    assert not javac_called, "javac fallback must never be reached after a Gradle containment-setup failure"


# --- 4: run_compile_check, Python - documents there is no containment
# call in this path at all (a pure in-process `compile()` syntax check),
# so there is nothing for a ContainmentSetupError to originate from here.

def test_python_compile_check_never_touches_containment(tmp_path):
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    validator.stack = "python"
    validator._run_cmd_with_timeout = _boom
    validator._run_maven_cmd = _boom
    result = validator.run_compile_check(["app.py"])
    assert result["success"] is True  # a real, ordinary syntax check - unaffected


# --- 5: run_compile_check, Ruby containment-setup failure ---

def test_ruby_compile_check_containment_failure_propagates(tmp_path):
    (tmp_path / "app.rb").write_text("puts 'hi'\n")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    validator.stack = "ruby"
    validator._run_cmd_with_timeout = _boom
    with pytest.raises(ContainmentSetupError):
        validator.run_compile_check(["app.rb"])


# --- run_pom_validate (found via this task's own ADJACENT TRACE requirement) ---

def test_run_pom_validate_containment_failure_cannot_pass(tmp_path):
    """Found while tracing the same anti-pattern elsewhere: this method's
    catch-all unconditionally returned success:True for ANY exception,
    an even more direct instance of the same SEC-002 defect than
    run_compile_check's conditional one."""
    validator = _contained_validator(tmp_path)
    validator._run_maven_cmd = _boom
    with pytest.raises(ContainmentSetupError):
        validator.run_pom_validate()


# --- 6: run_tests containment-setup failure propagates ---

def test_run_tests_python_containment_failure_propagates(tmp_path):
    (tmp_path / "test_app.py").write_text("def test_x():\n    assert True\n")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    validator.stack = "python"
    validator._resolve_python_interpreter = lambda: ("python3", None)
    validator._run_cmd_with_timeout = _boom
    with pytest.raises(ContainmentSetupError):
        validator.run_tests()


def test_run_tests_ruby_containment_failure_does_not_retry_plain_rspec(tmp_path):
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    validator.stack = "ruby"
    plain_rspec_called = []

    def _tracking_boom(cmd, *args, **kwargs):
        if cmd == ["rspec"]:
            plain_rspec_called.append(cmd)
        raise BackendUnavailableError("simulated: docker daemon unreachable")

    validator._run_cmd_with_timeout = _tracking_boom
    with pytest.raises(ContainmentSetupError):
        validator.run_tests()
    assert not plain_rspec_called, "must not retry via plain rspec after a containment-setup failure"


# --- 7: run_app containment-setup failure propagates ---

def test_run_app_containment_failure_propagates(tmp_path):
    validator = _contained_validator(tmp_path)
    validator._run_cmd_with_timeout = _boom
    with pytest.raises(ContainmentSetupError):
        validator.run_app(["python3", "app.py"])


# --- 8: run_app_sequence reaches infrastructure classification (unchanged
# by design - see this module's own docstring) ---

def test_run_app_sequence_containment_failure_reaches_infrastructure_classification(tmp_path):
    validator = _contained_validator(tmp_path)
    validator._run_cmd_with_timeout = _boom
    result = validator.run_app_sequence([["python3", "app.py"]], timeout=30)

    assert result["success"] is False
    assert result["steps"][0]["exit_code"] is None
    assert result["steps"][0]["timed_out"] is False
    reason = runtime_verification_infrastructure_reason(result)
    assert reason is not None, (
        "a swallowed containment-setup failure must still be recognized as an "
        "infrastructure failure - if this ever returns None, the failure would "
        "silently fall through to behavioral grading as if the app had genuinely run"
    )


# --- 9/10/11: ordinary failures remain ordinary failures (no regression) ---

def test_ordinary_maven_compile_failure_remains_ordinary_failure(tmp_path):
    validator = _contained_validator(tmp_path)
    (tmp_path / "Foo.java").write_text("public class Foo { this is not valid java }")
    validator._run_maven_cmd = lambda *a, **k: {
        "returncode": 1, "stdout": "", "stderr": "[ERROR] cannot find symbol",
    }
    result = validator.run_compile_check(["Foo.java"])
    assert result["success"] is False
    assert "Maven compilation failed" in result["output"]


def test_ordinary_test_failure_remains_ordinary_failure(tmp_path):
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    validator.stack = "python"
    validator._resolve_python_interpreter = lambda: ("python3", None)
    validator._run_cmd_with_timeout = lambda *a, **k: {"returncode": 1, "stdout": "1 failed", "stderr": ""}
    result = validator.run_tests()
    assert result["success"] is False


def test_ordinary_runtime_failure_remains_ordinary_failure(tmp_path):
    validator = _contained_validator(tmp_path)
    validator._run_cmd_with_timeout = lambda *a, **k: {
        "returncode": 1, "stdout": "", "stderr": "Traceback...", "timeout": False,
    }
    result = validator.run_app(["python3", "app.py"])
    assert result["success"] is False
    assert result["returncode"] == 1


# --- 12: missing/inapplicable toolchain behavior retains existing fallback ---

def test_maven_not_on_path_still_returns_failure_without_falling_through(tmp_path):
    """Pre-existing, intentional behavior (not touched by this fix):
    FileNotFoundError ('mvn' isn't on PATH) is a toolchain-applicability
    problem, not a containment-setup failure - it must still be returned
    directly, never silently swallowed into the javac fallback."""
    validator = _contained_validator(tmp_path)
    (tmp_path / "Foo.java").write_text("public class Foo { public static void main(String[] a) {} }")

    def _raise_fnf(*a, **k):
        raise FileNotFoundError("mvn: command not found")

    validator._run_maven_cmd = _raise_fnf
    result = validator.run_compile_check(["Foo.java"])
    assert result["success"] is False
    assert "Failed to invoke mvn compile" in result["output"]


def test_javac_fallback_still_works_when_maven_and_gradle_are_genuinely_inapplicable(tmp_path):
    """No pom.xml, no build.gradle at all - the Maven/Gradle blocks are
    both skipped entirely (not attempted, not failed), so the raw javac
    fallback must still run normally and succeed for valid Java source -
    unaffected by this fix, since no ContainmentSetupError is ever
    raised in this scenario."""
    (tmp_path / "Foo.java").write_text("public class Foo { public static void main(String[] a) {} }")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())
    validator.stack = "java"
    result = validator.run_compile_check(["Foo.java"])
    assert result["success"] is True, result["output"]

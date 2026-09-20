"""finite_command artifact preparation (2026-09-11) -
kriya/workflow/attempt.py::_prepare_finite_command_runtime_artifacts, the
finite_command counterpart to Managed Runtime Verification's own P6 fix
(kriya/tools/service_runtime.py). Real live incident this closes: a
`java -jar target/artemis-demo-1.0-SNAPSHOT.jar` finite_command failed with
"Unable to access jarfile" on every attempt, because neither the compile
gate (`mvn clean compile`) nor the test gate (`mvn test`) ever reaches
Maven's `package` phase.

Real subprocess execution via a fake executable `./mvnw` script (same
pattern as tests/test_service_runtime.py) - no LLM, no network, no real
Maven/JDK dependency.
"""
import os
import stat

import pytest

from kriya.workflow.attempt import _prepare_finite_command_runtime_artifacts
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.failure_grounding import classify_environment_failure


def _write_pom(tmp_path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")


def _write_executable(path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _write_mvnw(
    tmp_path, *, jar_relpath: str, exit_code: int = 0, create_jar: bool = True, stderr: str = "",
) -> None:
    """A real, executable ./mvnw stand-in - no Maven/JVM dependency, matching
    tests/test_service_runtime.py's own established pattern."""
    create_line = (
        f'mkdir -p "$(dirname "{jar_relpath}")" && touch "{jar_relpath}"'
        if create_jar else ": # no-op, artifact not created"
    )
    stderr_line = f'echo "{stderr}" 1>&2' if stderr else ": # no stderr"
    _write_executable(tmp_path / "mvnw", f"""#!/bin/sh
{create_line}
{stderr_line}
exit {exit_code}
""")


class _FakeState:
    def __init__(self):
        self.all_files_written = []
        self.attempt_number = 1
        self.gate_outcomes = []


class _FakeCtx:
    def __init__(self, worktree_path):
        self.worktree_path = str(worktree_path)
        self.established_files = []


class _FakeValidator:
    """Duck-typed stand-in for PolymorphicValidator - the function under
    test only ever calls build_subprocess_env_and_preexec() on it."""

    def __init__(self, env=None, preexec_fn=None):
        self._env = env
        self._preexec_fn = preexec_fn

    def build_subprocess_env_and_preexec(self):
        return self._env, self._preexec_fn


# --- E: no artifact required - unchanged, fast no-op ------------------------

def test_no_artifact_required_command_is_a_noop(tmp_path):
    """REQUIRED BEHAVIOR 4: preserve existing behavior for commands that
    don't need a packaged artifact."""
    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator()
    _prepare_finite_command_runtime_artifacts(
        [["python", "manage.py", "runserver"]], validator, ctx, state,
    )
    assert state.gate_outcomes == []


# --- B: artifact already current - no duplicate preparation -----------------

def test_artifact_already_current_skips_preparation(tmp_path):
    """REQUIRED BEHAVIOR 3: no ./mvnw exists at all here - if preparation
    tried to build, it would fall back to a bare 'mvn' not on this
    sandboxed PATH and fail loudly. A passing test therefore proves the
    already-current shortcut was actually taken, not an accidental rebuild
    success."""
    _write_pom(tmp_path)
    src = tmp_path / "src" / "main" / "java" / "Foo.java"
    src.parent.mkdir(parents=True)
    src.write_text("class Foo {}")
    os.utime(tmp_path / "pom.xml", (1000, 1000))
    os.utime(src, (1000, 1000))
    jar = tmp_path / "target" / "app.jar"
    jar.parent.mkdir(parents=True)
    jar.write_text("already built")
    os.utime(jar, (2000, 2000))

    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator(env={"PATH": str(tmp_path)})
    _prepare_finite_command_runtime_artifacts(
        [["java", "-jar", "target/app.jar"]], validator, ctx, state,
    )
    assert state.gate_outcomes == []


# --- A: artifact absent, build succeeds - preparation runs, no raise --------

def test_missing_artifact_is_prepared_successfully(tmp_path):
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar")
    jar = tmp_path / "target" / "app.jar"
    assert not jar.exists()

    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator()
    _prepare_finite_command_runtime_artifacts(
        [["java", "-jar", "target/app.jar"]], validator, ctx, state,
    )
    assert jar.is_file()
    assert state.gate_outcomes == []


def test_missing_artifact_via_classpath_flag_is_prepared(tmp_path):
    """The exact real shape a live run's own recovery attempt used
    (2026-09-11): `java -cp target/app.jar com.example.Main`."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar")
    jar = tmp_path / "target" / "app.jar"
    assert not jar.exists()

    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator()
    _prepare_finite_command_runtime_artifacts(
        [["java", "-cp", "target/app.jar", "com.example.Main"]], validator, ctx, state,
    )
    assert jar.is_file()
    assert state.gate_outcomes == []


# --- C: build fails due to real project defect - repair-eligible "compile" --

def test_build_failure_is_reported_as_compile_failure_not_environment(tmp_path):
    """REQUIRED BEHAVIOR 5: report the actual preparation/build failure and
    allow normal code-repair handling - never the misleading jar-not-found/
    ClassNotFound environment framing."""
    _write_pom(tmp_path)
    _write_mvnw(
        tmp_path, jar_relpath="target/app.jar", exit_code=1, create_jar=False,
        stderr="[ERROR] Non-parseable POM: bad element",
    )
    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator()
    with pytest.raises(QualityGateFailure) as exc_info:
        _prepare_finite_command_runtime_artifacts(
            [["java", "-jar", "target/app.jar"]], validator, ctx, state,
        )
    failure = exc_info.value.failure
    assert failure.type == "compile"
    assert "pom.xml" in failure.likely_files
    assert len(state.gate_outcomes) == 1
    # REQUIRED BEHAVIOR 6, checked at the source classify_environment_failure()
    # itself consults (retry_strategy.py's own downstream mapping) - this
    # message must never be reclassifiable as a generic environment gap.
    assert classify_environment_failure(failure.raw_output) is None
    assert classify_environment_failure(failure.message) is None


# --- G: build succeeds but artifact still missing - harness/naming puzzle ---

def test_artifact_materialization_failure_is_not_environment_or_doctor_eligible(tmp_path):
    """REQUIRED BEHAVIOR 6: Kriya's own inability to resolve the required
    artifact after a reportedly-successful build must not be classified as
    a generic user environment/toolchain failure."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar", exit_code=0, create_jar=False)
    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator()
    with pytest.raises(QualityGateFailure) as exc_info:
        _prepare_finite_command_runtime_artifacts(
            [["java", "-jar", "target/app.jar"]], validator, ctx, state,
        )
    failure = exc_info.value.failure
    assert failure.type == "package_preparation_failed"
    assert failure.type != "verification_infrastructure_failure"
    assert classify_environment_failure(failure.raw_output) is None
    assert classify_environment_failure(failure.message) is None


# --- D: genuine missing/broken toolchain - environment classification valid -

def test_missing_mvn_binary_remains_verification_infrastructure_failure(tmp_path):
    """No ./mvnw wrapper and 'mvn' not resolvable on the given PATH - a
    genuine toolchain gap. Must stay on the EXISTING environment/toolchain-
    eligible path, unchanged by this fix."""
    _write_pom(tmp_path)
    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator(env={"PATH": str(tmp_path)})  # no mvn anywhere on this PATH
    with pytest.raises(QualityGateFailure) as exc_info:
        _prepare_finite_command_runtime_artifacts(
            [["java", "-jar", "target/app.jar"]], validator, ctx, state,
        )
    failure = exc_info.value.failure
    assert failure.type == "verification_infrastructure_failure"


def test_no_pom_remains_verification_infrastructure_failure(tmp_path):
    """No pom.xml at all - no known build system to prepare anything -
    stays environment-eligible, matching _prepare_required_artifact's own
    existing (unchanged) behavior for this case."""
    state = _FakeState()
    ctx = _FakeCtx(tmp_path)
    validator = _FakeValidator()
    with pytest.raises(QualityGateFailure) as exc_info:
        _prepare_finite_command_runtime_artifacts(
            [["java", "-jar", "target/app.jar"]], validator, ctx, state,
        )
    failure = exc_info.value.failure
    assert failure.type == "verification_infrastructure_failure"

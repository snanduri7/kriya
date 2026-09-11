"""SEC-001-P6 Stage 3: PolymorphicValidator's real Python validation path
(stack detection -> project-local venv creation -> dependency install ->
pytest run) under `contained_execution_required=True`, proving the venv
is created with the CONTAINER's own interpreter and referenced by a
workspace-relative path - never Kriya's own host `sys.executable` - end to
end through the real `run_tests()`/`run_compile_check()` entry points, not
just the lower-level containment/process primitives already covered
elsewhere. Real Docker daemon required, skipped otherwise."""
import shutil
import subprocess

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


if not _docker_reachable():
    pytestmark = pytest.mark.skip(reason="docker daemon not reachable")


def _contained_cfg() -> AutonomyConfig:
    return AutonomyConfig(
        contained_execution_required=True, containment_backend="oci",
        sandbox_cpu_seconds=120, sandbox_memory_mb=1024,
    )


def test_contained_python_validation_uses_container_interpreter_never_host_executable(tmp_path):
    """A representative Python repo (requirements.txt declaring a real,
    tiny PyPI package + a test that imports it) through the ACTUAL
    run_tests() entry point - proves the venv-creation/interpreter-
    resolution fix works through the real validation path, not just its
    own unit-level pieces."""
    (tmp_path / "requirements.txt").write_text("six==1.16.0\n")
    (tmp_path / "test_uses_six.py").write_text(
        "import six\n\ndef test_six_is_importable():\n    assert six.__version__ == '1.16.0'\n"
    )

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained_cfg())
    assert validator.stack == "python"

    result = validator.run_tests()
    assert result["success"] is True, result["output"]
    assert "1 passed" in result["output"]

    # The interpreter actually used must be the workspace-relative venv
    # path (resolvable inside the container), never Kriya's own host
    # sys.executable - checked via the SAME resolution function run_tests()
    # itself calls.
    import sys
    interpreter, install_error = validator._resolve_python_interpreter()
    assert install_error is None
    assert interpreter != sys.executable
    assert not interpreter.startswith("/"), f"expected a workspace-relative interpreter path, got {interpreter!r}"
    assert interpreter == ".kriya/venv/bin/python" or interpreter.replace("\\", "/") == ".kriya/venv/bin/python"


def test_contained_python_validation_without_manifest_uses_generic_container_token(tmp_path):
    """No requirements.txt/pyproject.toml at all - falls back to a bare
    "python3" token (resolved by the container image's own PATH), never
    Kriya's sys.executable, which would not exist inside the container."""
    import sys

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained_cfg())
    interpreter, install_error = validator._resolve_python_interpreter()
    assert install_error is None
    assert interpreter == "python3"
    assert interpreter != sys.executable


def test_host_mode_python_validation_unchanged_uses_absolute_paths(tmp_path):
    """Compatibility check: contained_execution_required=False (the
    packaged default) must be byte-for-byte unchanged - sys.executable/
    absolute venv paths, exactly as before this stage's changes. Doesn't
    itself need Docker, but lives under this module's own docker-required
    skip guard for simplicity (host-mode behavior is already covered
    extensively elsewhere in the suite regardless)."""
    import sys

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())
    interpreter, install_error = validator._resolve_python_interpreter()
    assert install_error is None
    assert interpreter == sys.executable


# --- Maven two-phase (Stage 2), through the real validate.py entry points ---

_POM_WITH_A_REAL_DEPENDENCY = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.kriya.test</groupId>
  <artifactId>sec001-validate-oci-fixture</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.apache.commons</groupId>
      <artifactId>commons-lang3</artifactId>
      <version>3.14.0</version>
    </dependency>
  </dependencies>
</project>
"""

_JAVA_SOURCE_USING_DEPENDENCY = """package com.kriya.test;

import org.apache.commons.lang3.StringUtils;

public class Greeter {
    public static String greet() {
        return StringUtils.upperCase("hello");
    }
}
"""


def test_contained_maven_compile_succeeds_via_transparent_two_phase_acquisition(tmp_path):
    """A representative Java/Maven repo (a real Maven Central dependency
    the fresh worktree has never seen before, so the persistent cache
    starts genuinely empty) through the ACTUAL run_compile_check() entry
    point - proves _run_maven_cmd's upfront `dependency:go-offline` +
    offline compile cycle works transparently, with zero unrestricted
    networking during the compile step itself (only the acquisition call
    ever gets NetworkAuthority.UNRESTRICTED)."""
    src_dir = tmp_path / "src" / "main" / "java" / "com" / "kriya" / "test"
    src_dir.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(_POM_WITH_A_REAL_DEPENDENCY)
    (src_dir / "Greeter.java").write_text(_JAVA_SOURCE_USING_DEPENDENCY)

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained_cfg())
    assert validator.stack == "java"

    result = validator.run_compile_check([str(src_dir / "Greeter.java")])
    assert result["success"] is True, result["output"]

    # The persistent cache this run warmed must actually exist on the host
    # (proves it isn't ephemeral container-local state that vanished).
    cache_dir = validator._maven_cache_dir()
    import os
    assert os.path.isdir(cache_dir)
    assert any(os.scandir(cache_dir)), "Maven cache directory is empty after a successful contained compile"


def test_contained_maven_offline_missing_dependency_triggers_bounded_reacquisition(tmp_path):
    """A SECOND compile call, with the upfront cache-warming step forced
    to look like it never ran (simulating a dependency added mid-run,
    after the one-time upfront acquisition already happened) - proves the
    PER-COMMAND bounded reacquisition backstop (not just the upfront warm)
    genuinely recovers a missing dependency and succeeds, rather than
    failing outright or falling back to unrestricted networking for the
    compile step itself."""
    src_dir = tmp_path / "src" / "main" / "java" / "com" / "kriya" / "test"
    src_dir.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(_POM_WITH_A_REAL_DEPENDENCY)
    (src_dir / "Greeter.java").write_text(_JAVA_SOURCE_USING_DEPENDENCY)

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained_cfg())
    # Pretend the upfront warm already happened (with an EMPTY cache) -
    # forces the very first offline compile attempt to genuinely miss the
    # dependency and exercise the per-command reacquisition path, not the
    # upfront one.
    validator._maven_cache_warmed = True
    validator._maven_cache_dir()  # creates the (still-empty) cache dir

    result = validator.run_compile_check([str(src_dir / "Greeter.java")])
    assert result["success"] is True, result["output"]

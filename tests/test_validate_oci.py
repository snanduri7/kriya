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

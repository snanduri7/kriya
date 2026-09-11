"""SEC-001-P6: the two-phase dependency-execution module
(kriya/tools/dependency_execution.py) - deterministic offline-failure
classification (unit-level, no subprocess) plus real end-to-end Maven/
Python acquisition -> offline-execution proof against a live Docker
daemon (skipped entirely if unavailable)."""
import shutil
import subprocess

import pytest

from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.tools.dependency_execution import (
    OfflineFailureKind,
    _classify_maven_offline_failure,
    _classify_pip_offline_failure,
    maven_acquire_dependencies,
    maven_execute_offline,
    python_acquire_dependencies,
    python_execute_offline,
)
from kriya.tools.process import ProcessController, ProcessResult


def _result(returncode: int, stdout: str = "", stderr: str = "", timeout: bool = False) -> ProcessResult:
    return ProcessResult(returncode=returncode, stdout=stdout, stderr=stderr, timeout=timeout)


# --- deterministic classification (no subprocess) ---

def test_maven_offline_missing_dependency_classified():
    r = _result(1, stderr="Cannot access central (https://repo.maven.apache.org/maven2) in offline mode")
    assert _classify_maven_offline_failure(r) == OfflineFailureKind.MISSING_DEPENDENCY


def test_maven_ordinary_failure_not_classified_as_missing_dependency():
    r = _result(1, stderr="[ERROR] COMPILATION ERROR : \n[ERROR] cannot find symbol")
    assert _classify_maven_offline_failure(r) == OfflineFailureKind.ORDINARY_FAILURE


def test_maven_success_has_no_offline_failure_kind():
    r = _result(0, stdout="BUILD SUCCESS")
    assert _classify_maven_offline_failure(r) is None


def test_pip_offline_missing_dependency_classified():
    r = _result(1, stderr="ERROR: Could not find a version that satisfies the requirement doesnotexist123")
    assert _classify_pip_offline_failure(r) == OfflineFailureKind.MISSING_DEPENDENCY


def test_pip_ordinary_failure_not_classified_as_missing_dependency():
    r = _result(1, stderr="ImportError: cannot import name 'x' from 'y'")
    assert _classify_pip_offline_failure(r) == OfflineFailureKind.ORDINARY_FAILURE


# --- real end-to-end, live Docker daemon required ---

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


if not _docker_reachable():
    pytestmark = pytest.mark.skip(reason="docker daemon not reachable")


def test_python_two_phase_acquire_then_offline_execute(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "pip_cache"
    cache.mkdir()

    requirements = workspace / "requirements.txt"
    requirements.write_text("six==1.16.0\n")
    (workspace / "script.py").write_text("import six\nprint('six-ok', six.__version__)\n")

    controller = ProcessController()
    backend = OCIContainmentBackend()

    acquire_result = python_acquire_dependencies(
        str(workspace), "requirements.txt", str(cache), controller=controller, containment_backend=backend,
        timeout=120,
    )
    assert acquire_result.returncode == 0, acquire_result.stderr
    assert any(cache.iterdir()), "acquisition produced no cached wheels"

    outcome = python_execute_offline(
        str(workspace), str(cache), "requirements.txt", ["python3", "script.py"],
        controller=controller, containment_backend=backend, timeout=60,
    )
    assert outcome.offline_failure_kind is None
    assert outcome.succeeded, outcome.result.stderr
    assert "six-ok 1.16.0" in outcome.result.stdout


def test_python_offline_execute_without_acquisition_reports_missing_dependency(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    empty_cache = tmp_path / "empty_cache"
    empty_cache.mkdir()
    requirements = workspace / "requirements.txt"
    requirements.write_text("six==1.16.0\n")

    controller = ProcessController()
    backend = OCIContainmentBackend()

    outcome = python_execute_offline(
        str(workspace), str(empty_cache), "requirements.txt", ["python3", "-c", "import six"],
        controller=controller, containment_backend=backend, timeout=60,
    )
    assert not outcome.succeeded
    assert outcome.offline_failure_kind == OfflineFailureKind.MISSING_DEPENDENCY


def test_maven_two_phase_acquire_then_offline_execute(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache = tmp_path / "m2_cache"
    cache.mkdir()
    (workspace / "pom.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.kriya.test</groupId>
  <artifactId>sec001-oci-fixture</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
</project>
"""
    )

    controller = ProcessController()
    backend = OCIContainmentBackend()

    acquire_result = maven_acquire_dependencies(
        str(workspace), str(cache), controller=controller, containment_backend=backend, timeout=180,
    )
    assert acquire_result.returncode == 0, acquire_result.stderr

    outcome = maven_execute_offline(
        str(workspace), str(cache), ["validate"],
        controller=controller, containment_backend=backend, timeout=120,
    )
    assert outcome.offline_failure_kind is None
    assert outcome.succeeded, outcome.result.stderr


def test_maven_offline_execute_with_uncached_dependency_reports_missing_dependency(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    empty_cache = tmp_path / "empty_m2_cache"
    empty_cache.mkdir()
    (workspace / "pom.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.kriya.test</groupId>
  <artifactId>sec001-oci-fixture-missing-dep</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
  <dependencies>
    <dependency>
      <groupId>com.kriya.test</groupId>
      <artifactId>definitely-not-cached-anywhere</artifactId>
      <version>999.999.999</version>
    </dependency>
  </dependencies>
</project>
"""
    )

    controller = ProcessController()
    backend = OCIContainmentBackend()

    outcome = maven_execute_offline(
        str(workspace), str(empty_cache), ["dependency:resolve"],
        controller=controller, containment_backend=backend, timeout=60,
    )
    assert not outcome.succeeded
    assert outcome.offline_failure_kind == OfflineFailureKind.MISSING_DEPENDENCY, outcome.result.stdout + outcome.result.stderr

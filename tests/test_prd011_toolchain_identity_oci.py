"""PRD-011 Docker integration: actual runtime identity and digest evidence."""
import shutil
import subprocess

import pytest

from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.tools.process import ProcessController
from kriya.tools.toolchain_identity import ToolchainIdentity


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_reachable(), reason="Docker daemon not reachable")


@pytest.mark.parametrize(
    ("identity", "command", "expected"),
    [
        (
            ToolchainIdentity(
                "java", "jdk", "17", "maven", "3.9",
                "maven:3.9-eclipse-temurin-17", "fixture:pom.xml",
            ),
            ["java", "-version"],
            "17",
        ),
        (
            ToolchainIdentity(
                "python", "cpython", "3.12", "pip", None,
                "python:3.12-slim", "fixture:pyproject.toml",
            ),
            ["python3", "--version"],
            "3.12",
        ),
    ],
)
def test_actual_container_runtime_matches_profile_and_records_digest(tmp_path, identity, command, expected):
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION,
        workspace_path=str(tmp_path),
        network=NetworkAuthority.DENIED,
        toolchain_identity=identity,
    )
    result = ProcessController().run(
        command, cwd=str(tmp_path), timeout=120,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.returncode == 0, result.stderr
    evidence = result.toolchain_identity
    assert evidence["runtime_version"] == expected
    assert evidence["observed_runtime_version"] == expected
    assert evidence["image_digest"].startswith("sha256:")


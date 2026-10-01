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
            # JDK 8 reports "1.8.0_x": the attest must read it as 8.
            ToolchainIdentity(
                "java", "jdk", "8", "maven", "3.9",
                "maven:3.9-eclipse-temurin-8", "fixture:pom.xml",
            ),
            ["java", "-version"],
            "8",
        ),
        (
            # The Gradle build tool is attested too (major version 8).
            ToolchainIdentity(
                "java", "jdk", "17", "gradle", "8",
                "gradle:8-jdk17", "fixture:build.gradle",
            ),
            ["java", "-version"],  # the attest itself probes `gradle --version`
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
    # Java reports its feature release; Python its exact X.Y.Z (so a
    # patch-level requires-python bound can be checked against it).
    observed = evidence["observed_runtime_version"]
    assert observed.split(".")[:len(expected.split("."))] == expected.split(".")
    if identity.build_tool_version:
        assert evidence["observed_build_tool_version"].split(".")[0] == identity.build_tool_version.split(".")[0]
    assert evidence["image_digest"].startswith("sha256:")

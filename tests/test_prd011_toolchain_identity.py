import subprocess

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import ContainmentProfile, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend, _attest_toolchain_image
from kriya.tools.toolchain_identity import (
    ToolchainIdentity,
    ToolchainMismatchError,
    ToolchainResolutionError,
    resolve_toolchain_identity,
)
from kriya.tools.validate import PolymorphicValidator


def _pom(version: int) -> str:
    return f"""<project><modelVersion>4.0.0</modelVersion><properties>
<maven.compiler.release>{version}</maven.compiler.release>
</properties></project>"""


@pytest.mark.parametrize(
    ("version", "image"),
    [(17, "maven:3.9-eclipse-temurin-17"), (21, "maven:3.9-eclipse-temurin-21")],
)
def test_existing_validator_stack_detection_drives_versioned_jdk_profile(tmp_path, version, image):
    (tmp_path / "pom.xml").write_text(_pom(version))
    validator = PolymorphicValidator(
        str(tmp_path),
        autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
    )
    profile, _ = validator.build_containment_profile_and_backend()
    assert validator.stack == "java"
    assert profile.toolchain_identity.runtime_version == str(version)
    assert profile.toolchain_identity.containment_image == image


def test_conflicting_java_requirements_fail_before_container_execution(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.source>17</maven.compiler.source>"
        "<maven.compiler.target>21</maven.compiler.target></properties></project>"
    )
    with pytest.raises(ToolchainResolutionError, match="Conflicting Java"):
        PolymorphicValidator(
            str(tmp_path),
            autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
        )


def test_unsupported_java_requirement_fails_closed(tmp_path):
    (tmp_path / "pom.xml").write_text(_pom(11))
    with pytest.raises(ToolchainResolutionError, match="Java 11"):
        resolve_toolchain_identity(str(tmp_path), "java")


def test_selected_host_jdk_version_is_reused_for_containment(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    java_home = tmp_path / "jdk"
    java_home.mkdir()
    (java_home / "release").write_text('JAVA_VERSION="17.0.12"\n')
    identity = resolve_toolchain_identity(
        str(tmp_path), "java", java_home_override=str(java_home),
    )
    assert identity.runtime_version == "17"
    assert identity.requirement_source.startswith("JAVA_HOME:")


def test_python_requires_python_selects_compatible_versioned_image(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1"\nrequires-python = ">=3.11,<3.12"\n'
    )
    identity = resolve_toolchain_identity(str(tmp_path), "python")
    assert identity.runtime_version == "3.11"
    assert identity.containment_image == "python:3.11-slim"


def test_patch_specific_python_requirement_fails_closed(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1"\nrequires-python = "==3.11.7"\n'
    )
    with pytest.raises(ToolchainResolutionError, match="Patch-specific"):
        resolve_toolchain_identity(str(tmp_path), "python")


def test_oci_runtime_attestation_rejects_mismatched_jdk(monkeypatch):
    identity = ToolchainIdentity(
        "java", "jdk", "17", "maven", "3.9", "example/java17", "pom.xml",
    )
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, "sha256:abc\n", ""),
            subprocess.CompletedProcess([], 0, "", 'openjdk version "21.0.2"'),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: next(responses))
    with pytest.raises(ToolchainMismatchError, match="does not match"):
        _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False)


def test_oci_prepare_uses_resolved_image_and_persists_digest(tmp_path, monkeypatch):
    identity = ToolchainIdentity(
        "java", "jdk", "17", "maven", "3.9", "example/java17", "pom.xml",
    )
    resolved = identity.with_runtime_evidence(
        image_digest="sha256:resolved", observed_runtime_version="17",
    )
    backend = OCIContainmentBackend()
    monkeypatch.setattr(backend, "_require_docker", lambda: "docker")
    monkeypatch.setattr(backend, "_probe_daemon", lambda _docker: None)
    monkeypatch.setattr(
        "kriya.tools.containment_oci._attest_toolchain_image",
        lambda *args, **kwargs: resolved,
    )
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION,
        workspace_path=str(tmp_path),
        toolchain_identity=identity,
    )
    prepared = backend.prepare(profile, ["mvn", "test"])
    assert "example/java17" in prepared.command_prefix
    assert prepared.toolchain_identity.image_digest == "sha256:resolved"


# --- PRD-011 reopen: production callers assign java_home_override AFTER
# construction (workflow.py / attempt.py). The identity must follow it.

def _contained_cfg():
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci")


def _jdk_home(tmp_path, name, java_version):
    home = tmp_path / name
    home.mkdir()
    (home / "release").write_text(f'JAVA_VERSION="{java_version}"\n')
    return str(home)


def test_goal_stated_jdk_assigned_after_construction_selects_the_contained_image(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained_cfg())
    assert validator.toolchain_identity.runtime_version == "21"  # packaged default before the goal JDK
    validator.java_home_override = _jdk_home(tmp_path, "jdk17", "17.0.12")
    profile, _ = validator.build_containment_profile_and_backend()
    assert profile.toolchain_identity.runtime_version == "17"
    assert profile.toolchain_identity.containment_image == "maven:3.9-eclipse-temurin-17"
    assert profile.toolchain_identity.requirement_source.startswith("JAVA_HOME:")


def test_goal_stated_jdk_contradicting_the_repository_is_refused_on_assignment(tmp_path):
    (tmp_path / "pom.xml").write_text(_pom(17))
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained_cfg())
    with pytest.raises(ToolchainResolutionError, match="requires Java 17"):
        validator.java_home_override = _jdk_home(tmp_path, "jdk21", "21.0.4")


def test_clearing_the_override_restores_the_repository_identity(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=_contained_cfg(), java_home_override=_jdk_home(tmp_path, "jdk17", "17"),
    )
    assert validator.toolchain_identity.runtime_version == "17"
    validator.java_home_override = None
    assert validator.toolchain_identity.runtime_version == "21"


def test_uncontained_validator_never_resolves_a_toolchain_on_assignment(tmp_path):
    (tmp_path / "pom.xml").write_text(_pom(17))
    validator = PolymorphicValidator(str(tmp_path))
    validator.java_home_override = _jdk_home(tmp_path, "jdk21", "21")  # host mode: no containment identity
    assert validator.toolchain_identity is None
    assert validator.java_home_override.endswith("jdk21")

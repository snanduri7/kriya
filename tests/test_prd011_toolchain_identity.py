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
    (tmp_path / "pom.xml").write_text(_pom(7))
    with pytest.raises(ToolchainResolutionError, match="Java 7"):
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


def test_patch_specific_python_requirement_is_carried_to_attestation(tmp_path):
    # Undecidable from the 3.11 profile alone: resolved to 3.11 and the exact
    # patch the image really has is checked at attestation (below).
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1"\nrequires-python = "==3.11.7"\n'
    )
    identity = resolve_toolchain_identity(str(tmp_path), "python")
    assert identity.runtime_version == "3.11"
    assert identity.runtime_constraint == "==3.11.7"


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
    # Runs the attested content by its immutable ID, never the movable tag.
    assert "sha256:resolved" in prepared.command_prefix
    assert "example/java17" not in prepared.command_prefix
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


# --- PRD-011 reopen: profile coverage and exact attestation.

@pytest.mark.parametrize("version", [8, 11, 17, 21])
def test_every_lts_jdk_has_versioned_maven_and_gradle_profiles(tmp_path, version):
    (tmp_path / "pom.xml").write_text(_pom(version))
    maven = resolve_toolchain_identity(str(tmp_path), "java")
    assert (maven.runtime_version, maven.containment_image) == (str(version), f"maven:3.9-eclipse-temurin-{version}")
    (tmp_path / "pom.xml").unlink()
    (tmp_path / "build.gradle").write_text(f"java {{ toolchain {{ languageVersion = JavaLanguageVersion.of({version}) }} }}")
    gradle = resolve_toolchain_identity(str(tmp_path), "java")
    assert (gradle.containment_image, gradle.build_tool, gradle.build_tool_version) == (
        f"gradle:8-jdk{version}", "gradle", "8",
    )


def test_legacy_1_x_java_version_in_pom_maps_to_jdk8(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.source>1.8</maven.compiler.source>"
        "<maven.compiler.target>1.8</maven.compiler.target></properties></project>"
    )
    assert resolve_toolchain_identity(str(tmp_path), "java").runtime_version == "8"


def _requires(tmp_path, spec):
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "p"\nrequires-python = "{spec}"\n')
    return resolve_toolchain_identity(str(tmp_path), "python")


@pytest.mark.parametrize(("spec", "expected"), [
    (">=3.9,<4", "3.12"),       # common open range: the long-standing default
    ("!=3.9.*", "3.12"),
    (">=3.8.1", "3.12"),        # a patch bound below every profile is decidable
    ("~=3.10", "3.12"),
    ("<3.12,>=3.10", "3.11"),   # nearest supported minor to the default
    (">=3.13", "3.13"),
    (">3.12", "3.13"),          # every 3.13 patch satisfies; only some 3.12 do
    ("==3.14.*", "3.14"),
    ("==3.10.*", "3.10"),
    (">=3", "3.12"),            # major-only bound
])
def test_common_requires_python_specs_resolve_without_a_constraint(tmp_path, spec, expected):
    identity = _requires(tmp_path, spec)
    assert identity.runtime_version == expected
    assert identity.runtime_constraint is None


def test_same_minor_patch_bound_is_verified_at_attestation(tmp_path):
    identity = _requires(tmp_path, ">=3.11.4,<3.12")
    assert (identity.runtime_version, identity.runtime_constraint) == ("3.11", ">=3.11.4,<3.12")


@pytest.mark.parametrize("spec", ["<3.10", ">=3.15", "==3.9.*", "===3.11", ">=3.11rc1", "==3.12.*.1"])
def test_unsatisfiable_or_unsupported_specs_fail_closed(tmp_path, spec):
    with pytest.raises(ToolchainResolutionError):
        _requires(tmp_path, spec)


def test_python_version_file_pins_the_minor_and_records_the_exact_pin(tmp_path):
    (tmp_path / ".python-version").write_text("3.11.7\n")
    identity = resolve_toolchain_identity(str(tmp_path), "python")
    assert identity.runtime_version == "3.11"
    assert identity.runtime_constraint is None
    assert identity.requirement_source == ".python-version:3.11.7"


def test_unsupported_python_version_file_fails_closed(tmp_path):
    (tmp_path / ".python-version").write_text("3.9.18\n")
    with pytest.raises(ToolchainResolutionError):
        resolve_toolchain_identity(str(tmp_path), "python")


class _FakeDocker:
    """subprocess.run stand-in: `image inspect` -> digest; `run` -> the
    version text for the entrypoint. Records every argv."""

    def __init__(self, digest, outputs):
        self.digest, self.outputs, self.calls = digest, outputs, []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, self.digest + "\n", "")
        entrypoint = argv[argv.index("--entrypoint") + 1]
        return subprocess.CompletedProcess(argv, 0, "", self.outputs[entrypoint])


def _unique_digest(label):
    import hashlib
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def test_jdk8_image_attests_from_its_1_8_version_string(monkeypatch):
    identity = ToolchainIdentity("java", "jdk", "8", "maven", "3.9", "maven:3.9-eclipse-temurin-8", "pom.xml")
    docker = _FakeDocker(_unique_digest("jdk8"), {
        "java": 'openjdk version "1.8.0_442"', "mvn": "Apache Maven 3.9.9 (abc)",
    })
    monkeypatch.setattr(subprocess, "run", docker)
    resolved = _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False)
    assert (resolved.observed_runtime_version, resolved.observed_build_tool_version) == ("8", "3.9.9")
    # Every probe ran the attested content digest, never the tag.
    probes = [call for call in docker.calls if call[1] == "run"]
    assert probes and all(docker.digest in call and identity.containment_image not in call for call in probes)


def test_gradle_build_tool_is_attested(monkeypatch):
    identity = ToolchainIdentity("java", "jdk", "17", "gradle", "8", "gradle:8-jdk17", "build.gradle")
    good = _FakeDocker(_unique_digest("gradle-ok"), {"java": 'openjdk version "17.0.12"', "gradle": "Gradle 8.10.2"})
    monkeypatch.setattr(subprocess, "run", good)
    assert _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False) \
        .observed_build_tool_version == "8.10.2"
    wrong = _FakeDocker(_unique_digest("gradle-7"), {"java": 'openjdk version "17.0.12"', "gradle": "Gradle 7.6.4"})
    monkeypatch.setattr(subprocess, "run", wrong)
    with pytest.raises(ToolchainMismatchError, match="gradle 8"):
        _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False)


@pytest.mark.parametrize(("observed", "accepted"), [("3.11.7", True), ("3.11.9", False)])
def test_python_patch_constraint_is_checked_against_the_observed_runtime(monkeypatch, observed, accepted):
    identity = ToolchainIdentity(
        "python", "cpython", "3.11", "pip", None, "python:3.11-slim", "pyproject.toml",
        runtime_constraint="==3.11.7",
    )
    monkeypatch.setattr(subprocess, "run", _FakeDocker(_unique_digest(observed), {"python3": f"Python {observed}"}))
    if accepted:
        resolved = _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False)
        assert resolved.observed_runtime_version == "3.11.7"
    else:
        with pytest.raises(ToolchainMismatchError, match="does not satisfy the declared constraint"):
            _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False)


def test_python_minor_mismatch_is_refused(monkeypatch):
    identity = ToolchainIdentity("python", "cpython", "3.12", "pip", None, "python:3.12-slim", "default")
    monkeypatch.setattr(subprocess, "run", _FakeDocker(_unique_digest("py313"), {"python3": "Python 3.13.1"}))
    with pytest.raises(ToolchainMismatchError, match="does not match"):
        _attest_toolchain_image("docker", identity.containment_image, identity, allow_pull=False)

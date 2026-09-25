"""PRD-011 binds the toolchain resume fingerprint PRD-008 left UNAVAILABLE.

Contained execution: the versioned profile PolymorphicValidator resolves plus
the local image content digest (inspected, never pulled or run). Host mode:
UNAVAILABLE, exactly as before - every reuse decision treats that as
UNVERIFIED, never a match."""
import pytest

from kriya.config.config import AppConfig, AutonomyConfig
from kriya.tools import containment_oci
from kriya.workflow import toolchain as workflow_toolchain
from kriya.workflow.milestone_completion import current_toolchain_identity
from kriya.workflow.resume_fingerprints import (
    FingerprintStatus,
    compare_resume_fingerprints,
    fingerprint_block,
    generation_resume_fingerprints,
    toolchain_fingerprint,
)

DIGEST_1 = "sha256:" + "1" * 64
DIGEST_2 = "sha256:" + "2" * 64


def _pom(version):
    return (
        "<project><modelVersion>4.0.0</modelVersion><properties>"
        f"<maven.compiler.release>{version}</maven.compiler.release></properties></project>"
    )


def _contained():
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci")


@pytest.fixture
def local_images(monkeypatch):
    """image tag -> content digest of what is present locally."""
    present = {}
    inspected = []

    def fake_digest(image):
        inspected.append(image)
        return present.get(image)

    monkeypatch.setattr(containment_oci, "local_image_content_digest", fake_digest)
    present["inspected"] = inspected
    return present


def test_host_mode_toolchain_stays_unavailable(tmp_path, local_images):
    (tmp_path / "pom.xml").write_text(_pom(17))
    fingerprint = toolchain_fingerprint(str(tmp_path), AutonomyConfig())
    assert not fingerprint.available
    assert "containment not required" in fingerprint.basis
    assert local_images["inspected"] == []  # host mode never touches Docker


def test_contained_toolchain_binds_profile_and_image_digest(tmp_path, local_images):
    (tmp_path / "pom.xml").write_text(_pom(17))
    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_1
    first = toolchain_fingerprint(str(tmp_path), _contained())
    assert first.available and first.basis == "contained-toolchain-image"
    assert toolchain_fingerprint(str(tmp_path), _contained()) == first  # deterministic

    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_2  # the tag now names other content
    assert toolchain_fingerprint(str(tmp_path), _contained()).value != first.value


def test_required_jdk_change_changes_the_fingerprint(tmp_path, local_images):
    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_1
    local_images["maven:3.9-eclipse-temurin-21"] = DIGEST_1
    (tmp_path / "pom.xml").write_text(_pom(17))
    jdk17 = toolchain_fingerprint(str(tmp_path), _contained())
    (tmp_path / "pom.xml").write_text(_pom(21))
    assert toolchain_fingerprint(str(tmp_path), _contained()).value != jdk17.value


def test_missing_image_or_unresolvable_requirement_is_unavailable(tmp_path, local_images):
    (tmp_path / "pom.xml").write_text(_pom(17))
    missing = toolchain_fingerprint(str(tmp_path), _contained())
    assert not missing.available and "not present locally" in missing.basis
    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.source>17</maven.compiler.source>"
        "<maven.compiler.target>21</maven.compiler.target></properties></project>"
    )
    conflicting = toolchain_fingerprint(str(tmp_path), _contained())
    assert not conflicting.available and "unresolvable" in conflicting.basis


def test_unknown_stack_is_unavailable(tmp_path, local_images):
    fingerprint = toolchain_fingerprint(str(tmp_path), _contained())
    assert not fingerprint.available


def test_goal_stated_jdk_is_part_of_the_identity(tmp_path, local_images, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project/>")
    home = tmp_path / "jdk17"
    home.mkdir()
    (home / "release").write_text('JAVA_VERSION="17.0.2"\n')
    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_1
    local_images["maven:3.9-eclipse-temurin-21"] = DIGEST_1
    monkeypatch.setattr(
        workflow_toolchain, "_resolve_java_home_override",
        lambda goal: str(home) if "Java 17" in goal else None,
    )
    with_goal = toolchain_fingerprint(str(tmp_path), _contained(), goal="Fix it on Java 17")
    without = toolchain_fingerprint(str(tmp_path), _contained(), goal="Fix it")
    assert with_goal.available and without.available
    assert with_goal.value != without.value


def test_direct_resume_detects_a_toolchain_image_change(tmp_path, local_images):
    (tmp_path / "pom.xml").write_text(_pom(17))
    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_1
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "oci"
    stored = fingerprint_block(generation_resume_fingerprints(cfg, str(tmp_path), goal="g"))

    same = generation_resume_fingerprints(cfg, str(tmp_path), goal="g")
    by_name = {c.name: c for c in compare_resume_fingerprints(stored, same, ["candidate_gate_outcomes"])}
    assert by_name["toolchain"].status is FingerprintStatus.MATCH

    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_2
    changed = generation_resume_fingerprints(cfg, str(tmp_path), goal="g")
    by_name = {c.name: c for c in compare_resume_fingerprints(stored, changed, ["candidate_gate_outcomes"])}
    assert by_name["toolchain"].status is FingerprintStatus.CHANGED
    assert by_name["toolchain"].invalidates


def test_direct_resume_in_host_mode_stays_unverified(tmp_path, local_images):
    (tmp_path / "pom.xml").write_text(_pom(17))
    cfg = AppConfig()
    stored = fingerprint_block(generation_resume_fingerprints(cfg, str(tmp_path), goal="g"))
    current = generation_resume_fingerprints(cfg, str(tmp_path), goal="g")
    by_name = {c.name: c for c in compare_resume_fingerprints(stored, current, ["candidate_gate_outcomes"])}
    assert by_name["toolchain"].status is FingerprintStatus.UNVERIFIED


def test_milestone_identity_uses_the_same_function(tmp_path, local_images):
    (tmp_path / "pom.xml").write_text(_pom(17))
    local_images["maven:3.9-eclipse-temurin-17"] = DIGEST_1
    identity = current_toolchain_identity(str(tmp_path), _contained())
    assert identity == toolchain_fingerprint(str(tmp_path), _contained()).to_dict()
    assert current_toolchain_identity(str(tmp_path), AutonomyConfig())["value"] == "UNAVAILABLE"

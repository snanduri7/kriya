"""MA5.4: ArtifactRegistry (kriya/control/artifacts.py) - real deterministic
derivation from actual pom.xml/pyproject.toml/package.json files written to
a real temp workspace (never mocked - the whole point is proving these are
REAL parsed facts, not guesses), parent-inheritance resolution, multi-
module discovery, and drift detection."""

import os
import tempfile

import pytest

from kriya.control.artifacts import ArtifactRegistry, derive_maven_artifact


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


_SIMPLE_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>my-app</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
</project>
"""

_PARENT_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>parent-pom</artifactId>
  <version>2.0.0</version>
  <packaging>pom</packaging>
  <modules>
    <module>child</module>
  </modules>
</project>
"""

_CHILD_POM_NO_GROUP_OR_VERSION = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>com.example</groupId>
    <artifactId>parent-pom</artifactId>
    <version>2.0.0</version>
  </parent>
  <artifactId>child-module</artifactId>
</project>
"""


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# --- Maven derivation ---

def test_derives_a_simple_pom_directly(workspace):
    pom_path = os.path.join(workspace, "pom.xml")
    _write(pom_path, _SIMPLE_POM)
    record = derive_maven_artifact(workspace, pom_path, milestone_id="M1")
    assert record is not None
    assert record.ecosystem == "maven"
    assert record.coordinates == {"artifactId": "my-app", "groupId": "com.example", "version": "1.0.0"}
    assert record.packaging == "jar"
    assert record.module_path == ""


def test_defaults_packaging_to_jar_when_absent(workspace):
    pom_path = os.path.join(workspace, "pom.xml")
    _write(pom_path, _SIMPLE_POM.replace("<packaging>jar</packaging>", ""))
    record = derive_maven_artifact(workspace, pom_path, milestone_id="M1")
    assert record.packaging == "jar"


def test_resolves_group_id_and_version_from_parent_not_assumed(workspace):
    """The hard requirement: a child pom missing its own groupId/version
    must resolve them from the real <parent> block, not guess or omit."""
    child_pom_path = os.path.join(workspace, "child", "pom.xml")
    _write(child_pom_path, _CHILD_POM_NO_GROUP_OR_VERSION)
    record = derive_maven_artifact(workspace, child_pom_path, milestone_id="M1")
    assert record.coordinates["groupId"] == "com.example"
    assert record.coordinates["version"] == "2.0.0"
    assert record.coordinates["artifactId"] == "child-module"


def test_detects_real_resource_roots_that_exist_on_disk(workspace):
    pom_path = os.path.join(workspace, "pom.xml")
    _write(pom_path, _SIMPLE_POM)
    os.makedirs(os.path.join(workspace, "src", "main", "java"))
    os.makedirs(os.path.join(workspace, "src", "main", "resources"))
    record = derive_maven_artifact(workspace, pom_path, milestone_id="M1")
    assert os.path.join("src", "main", "java") in record.resource_roots
    assert os.path.join("src", "main", "resources") in record.resource_roots
    assert os.path.join("src", "test", "java") not in record.resource_roots


def test_returns_none_for_a_missing_pom(workspace):
    assert derive_maven_artifact(workspace, os.path.join(workspace, "pom.xml"), "M1") is None


def test_returns_none_for_malformed_xml(workspace):
    pom_path = os.path.join(workspace, "pom.xml")
    _write(pom_path, "<project><not-closed>")
    assert derive_maven_artifact(workspace, pom_path, "M1") is None


def test_returns_none_when_artifact_id_missing(workspace):
    pom_path = os.path.join(workspace, "pom.xml")
    _write(pom_path, "<project xmlns='http://maven.apache.org/POM/4.0.0'><groupId>x</groupId></project>")
    assert derive_maven_artifact(workspace, pom_path, "M1") is None


# --- Multi-module discovery via ArtifactRegistry.derive_from_workspace ---

def test_multi_module_discovery_finds_parent_and_child(workspace):
    _write(os.path.join(workspace, "pom.xml"), _PARENT_POM)
    _write(os.path.join(workspace, "child", "pom.xml"), _CHILD_POM_NO_GROUP_OR_VERSION)

    registry = ArtifactRegistry()
    records = registry.derive_from_workspace(workspace, milestone_id="M1")
    module_paths = {r.module_path for r in records}
    assert "" in module_paths
    assert "child" in module_paths
    child = next(r for r in records if r.module_path == "child")
    assert child.coordinates["groupId"] == "com.example"


# --- Python / npm derivation ---

def test_derives_python_artifact_from_pyproject(workspace):
    _write(os.path.join(workspace, "pyproject.toml"), '[project]\nname = "myapp"\nversion = "0.1.0"\n')
    registry = ArtifactRegistry()
    records = registry.derive_from_workspace(workspace, milestone_id="M1")
    python_records = [r for r in records if r.ecosystem == "python"]
    assert len(python_records) == 1
    assert python_records[0].coordinates == {"name": "myapp", "version": "0.1.0"}


def test_derives_npm_artifact_from_package_json(workspace):
    _write(os.path.join(workspace, "package.json"), '{"name": "my-npm-pkg", "version": "3.0.0"}')
    registry = ArtifactRegistry()
    records = registry.derive_from_workspace(workspace, milestone_id="M1")
    npm_records = [r for r in records if r.ecosystem == "npm"]
    assert len(npm_records) == 1
    assert npm_records[0].coordinates == {"name": "my-npm-pkg", "version": "3.0.0"}


def test_no_derivation_for_an_ecosystem_with_no_marker_file(workspace):
    """An empty workspace yields nothing - never a fabricated record."""
    registry = ArtifactRegistry()
    assert registry.derive_from_workspace(workspace, milestone_id="M1") == ()


# --- record / resolve_for_milestone / invalidate ---

def test_record_and_resolve_for_milestone(workspace):
    registry = ArtifactRegistry()
    _write(os.path.join(workspace, "pom.xml"), _SIMPLE_POM)
    for record in registry.derive_from_workspace(workspace, "M1"):
        registry.record(record)
    resolved = registry.resolve_for_milestone("M1")
    assert len(resolved) == 1
    assert resolved[0].coordinates["artifactId"] == "my-app"


def test_invalidate_clears_a_milestones_records(workspace):
    registry = ArtifactRegistry()
    _write(os.path.join(workspace, "pom.xml"), _SIMPLE_POM)
    for record in registry.derive_from_workspace(workspace, "M1"):
        registry.record(record)
    registry.invalidate("M1")
    assert registry.resolve_for_milestone("M1") == ()


# --- validate() / drift detection ---

def test_validate_reports_no_drift_when_workspace_unchanged(workspace):
    registry = ArtifactRegistry()
    _write(os.path.join(workspace, "pom.xml"), _SIMPLE_POM)
    for record in registry.derive_from_workspace(workspace, "M1"):
        registry.record(record)
    results = registry.validate(workspace, "M1")
    assert len(results) == 1
    assert results[0].drifted is False
    assert results[0].reason_code is None


def test_validate_detects_coordinate_drift(workspace):
    pom_path = os.path.join(workspace, "pom.xml")
    _write(pom_path, _SIMPLE_POM)
    registry = ArtifactRegistry()
    for record in registry.derive_from_workspace(workspace, "M1"):
        registry.record(record)

    # The workspace's pom.xml changes underneath the registry - e.g. a
    # later milestone bumped the version without updating the registry.
    _write(pom_path, _SIMPLE_POM.replace("1.0.0", "2.0.0"))
    results = registry.validate(workspace, "M1")
    assert results[0].drifted is True
    assert results[0].reason_code == "ARTIFACT_DRIFT"
    assert results[0].current.coordinates["version"] == "2.0.0"


def test_validate_detects_a_recorded_artifact_that_disappeared():
    with tempfile.TemporaryDirectory() as workspace:
        pom_path = os.path.join(workspace, "pom.xml")
        _write(pom_path, _SIMPLE_POM)
        registry = ArtifactRegistry()
        for record in registry.derive_from_workspace(workspace, "M1"):
            registry.record(record)
        os.remove(pom_path)
        results = registry.validate(workspace, "M1")
        assert results[0].drifted is True
        assert results[0].current is None


# --- persistence round trip (dict shape) ---

def test_to_dict_from_dict_round_trips(workspace):
    registry = ArtifactRegistry()
    _write(os.path.join(workspace, "pom.xml"), _SIMPLE_POM)
    for record in registry.derive_from_workspace(workspace, "M1"):
        registry.record(record)

    reloaded = ArtifactRegistry.from_dict(registry.to_dict())
    resolved = reloaded.resolve_for_milestone("M1")
    assert len(resolved) == 1
    assert resolved[0].coordinates["artifactId"] == "my-app"

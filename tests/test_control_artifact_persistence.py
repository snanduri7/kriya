"""MA5.4: ArtifactRegistry file persistence."""

import os
import tempfile

import pytest

from kriya.control.artifacts import ArtifactRecord, ArtifactRegistry, ArtifactRegistryCorruptError
from kriya.control.persistence import (
    artifact_registry_path,
    load_artifact_registry,
    save_artifact_registry,
)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_load_returns_empty_registry_when_never_saved(workspace):
    assert load_artifact_registry(workspace).all_records() == ()


def test_save_and_load_round_trips(workspace):
    registry = ArtifactRegistry()
    registry.record(ArtifactRecord(
        milestone_id="M1", ecosystem="maven", kind="library",
        module_path="", coordinates={"artifactId": "app", "groupId": "com.example", "version": "1.0"},
        packaging="jar",
    ))
    save_artifact_registry(workspace, registry)
    loaded = load_artifact_registry(workspace)
    resolved = loaded.resolve_for_milestone("M1")
    assert len(resolved) == 1
    assert resolved[0].coordinates["artifactId"] == "app"


def test_load_fails_closed_on_corrupt_file(workspace):
    """Fails closed means a typed refusal that leaves the bytes alone. (It used to return an EMPTY registry - the defect ARTIFACT-REGISTRY-MALFORMED-READ-AS-EMPTY-001: drift detection went silently
    off and the next save overwrote the evidence. Full matrix: tests/test_artifact_registry_strict_read.py.)"""
    path = artifact_registry_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("not json")
    with pytest.raises(ArtifactRegistryCorruptError):
        load_artifact_registry(workspace)
    with open(path) as f:
        assert f.read() == "not json"

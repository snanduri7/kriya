"""Release certification identity (PLAT-RELEASE-IDENTITY-001).

A recorded certification is CURRENT only while the Kriya revision, the
platform providers and capability statuses, the containment backend and the
environment are all unchanged; any material change is STALE (re-run
required), with every changed field named. A dirty or revision-less record
is never CURRENT, and a tampered digest is detected.
"""
import dataclasses
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from kriya.core import release_identity as ri
from kriya.platform import services


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "kriya-src"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _record(source, backend="none"):
    return json.loads(json.dumps(ri.release_identity(backend, source_root=str(source))))


def test_an_unchanged_host_is_current_and_the_record_carries_every_material_field(source):
    recorded = _record(source)
    assert set(recorded) == {"version", "kriya", "platform", "containment", "environment", "digest"}
    assert recorded["kriya"]["dirty"] is False and len(recorded["kriya"]["revision"]) == 40
    assert set(recorded["platform"]["providers"]) == {"workspace_lock", "resource_limits", "host_identity",
                                                    "process_control"}
    assert ri.compare_release_identity(recorded, ri.release_identity("none", source_root=str(source))) == {
        "status": ri.CURRENT, "changes": []}


def test_a_new_revision_is_stale(source):
    recorded = _record(source)
    (source / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "next"], cwd=source, check=True)
    result = ri.compare_release_identity(recorded, ri.release_identity("none", source_root=str(source)))
    assert result == {"status": ri.STALE, "changes": ["kriya.revision"]}


def test_a_dirty_or_unversioned_record_is_never_current(source, tmp_path):
    (source / "a.py").write_text("uncommitted\n")
    dirty = _record(source)
    assert dirty["kriya"]["dirty"] is True
    assert "kriya.unpinned" in ri.compare_release_identity(dirty, dirty)["changes"]
    plain = tmp_path / "no-git"
    plain.mkdir()
    unversioned = _record(plain)
    assert unversioned["kriya"] == {"revision": "unavailable", "dirty": None}
    assert ri.compare_release_identity(unversioned, unversioned)["status"] == ri.STALE


def test_a_different_platform_provider_is_stale(source):
    recorded = _record(source)

    class OtherLock:
        name = "other-lock"

        def capability(self):
            return services.compose(services.POSIX).workspace_lock.capability()

    with services.override(dataclasses.replace(services.compose(services.POSIX), workspace_lock=OtherLock())):
        result = ri.compare_release_identity(recorded, ri.release_identity("none", source_root=str(source)))
    assert result["status"] == ri.STALE and "platform.providers.workspace_lock" in result["changes"]


def test_a_changed_capability_status_is_stale(source):
    with patch.object(sys, "platform", "linux"):
        recorded = _record(source)
    with patch.object(sys, "platform", "darwin"):
        result = ri.compare_release_identity(recorded, ri.release_identity("none", source_root=str(source)))
    assert result == {"status": ri.STALE, "changes": ["platform.capabilities"]}


def test_a_different_containment_backend_or_environment_is_stale(source):
    recorded = _record(source)
    assert "containment.backend" in ri.compare_release_identity(
        recorded, ri.release_identity("oci", source_root=str(source)))["changes"]
    with patch.object(ri.platform, "python_version", lambda: "9.9.9"):
        assert ri.compare_release_identity(recorded, ri.release_identity("none", source_root=str(source)))[
            "changes"] == ["environment.python"]


def test_a_tampered_record_is_stale(source):
    recorded = _record(source)
    forged = {**recorded, "containment": {"backend": "oci", "runtime_version": "28.0.4"}}
    result = ri.compare_release_identity(forged, {**forged})
    assert result["status"] == ri.STALE and result["changes"] == ["digest"]

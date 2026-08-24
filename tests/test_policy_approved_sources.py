"""MA4.11: approved-source promotion (kriya/policy/approved_sources.py).
Covers the manifest round trip, the "hash mismatch loses elevated trust
outright" requirement that's the whole point of this mechanism, and the
"never downgrades an already-higher default" invariant."""

import json
import os
import tempfile

import pytest

from kriya.policy.approved_sources import (
    ApprovedSourceEntry,
    compute_sha256,
    load_manifest,
    promote_source,
    resolve_trust_level,
    save_manifest,
)
from kriya.policy.trust import TrustLevel


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_compute_sha256_matches_hashlib_directly():
    import hashlib
    assert compute_sha256(b"hello") == hashlib.sha256(b"hello").hexdigest()


def test_load_manifest_is_empty_for_a_workspace_with_no_approvals(workspace):
    assert load_manifest(workspace) == {}


def test_load_manifest_tolerates_malformed_json_and_fails_closed(workspace):
    manifest_dir = os.path.join(workspace, ".kriya", "policy")
    os.makedirs(manifest_dir, exist_ok=True)
    with open(os.path.join(manifest_dir, "approved-sources.json"), "w") as f:
        f.write("{not valid json")
    assert load_manifest(workspace) == {}


def test_save_and_load_manifest_round_trips(workspace):
    entries = {
        "skills/rules.txt": ApprovedSourceEntry(path="skills/rules.txt", sha256="abc123", note="reviewed 2026-08-24"),
    }
    save_manifest(workspace, entries)
    loaded = load_manifest(workspace)
    assert loaded == entries

    on_disk = os.path.join(workspace, ".kriya", "policy", "approved-sources.json")
    with open(on_disk) as f:
        raw = json.load(f)
    assert raw == {"skills/rules.txt": {"sha256": "abc123", "note": "reviewed 2026-08-24"}}


def test_promote_source_creates_manifest_and_directory(workspace):
    entry = promote_source(workspace, "skills/rules.txt", b"the approved content", note="initial approval")
    assert entry.sha256 == compute_sha256(b"the approved content")
    assert load_manifest(workspace) == {"skills/rules.txt": entry}


def test_promote_source_upserts_rather_than_duplicating(workspace):
    promote_source(workspace, "skills/rules.txt", b"version one")
    second = promote_source(workspace, "skills/rules.txt", b"version two", note="re-approved")
    entries = load_manifest(workspace)
    assert len(entries) == 1
    assert entries["skills/rules.txt"] == second
    assert entries["skills/rules.txt"].sha256 == compute_sha256(b"version two")


def test_promote_source_normalizes_the_path(workspace):
    promote_source(workspace, "./skills/rules.txt", b"content")
    promote_source(workspace, "skills\\rules.txt", b"content v2")
    entries = load_manifest(workspace)
    assert list(entries.keys()) == ["skills/rules.txt"]


def test_resolve_trust_level_promotes_on_exact_hash_match(workspace):
    content = b"the exact approved bytes"
    promote_source(workspace, "skills/rules.txt", content)
    result = resolve_trust_level(workspace, "skills/rules.txt", content, default_level=TrustLevel.REPOSITORY)
    assert result == TrustLevel.APPROVED_PROJECT_POLICY


def test_resolve_trust_level_falls_back_when_path_was_never_approved(workspace):
    result = resolve_trust_level(workspace, "skills/rules.txt", b"anything", default_level=TrustLevel.REPOSITORY)
    assert result == TrustLevel.REPOSITORY


def test_resolve_trust_level_loses_elevated_trust_on_hash_mismatch(workspace):
    """The core requirement: content changed since it was approved must NOT
    keep riding on the old approval - one byte of drift is a full loss of
    the elevated rung, straight back to default_level."""
    promote_source(workspace, "skills/rules.txt", b"the original approved bytes")
    tampered = b"the original approved bytes, but modified"
    result = resolve_trust_level(workspace, "skills/rules.txt", tampered, default_level=TrustLevel.REPOSITORY)
    assert result == TrustLevel.REPOSITORY
    assert result != TrustLevel.APPROVED_PROJECT_POLICY


def test_resolve_trust_level_never_downgrades_an_already_higher_default(workspace):
    """A real user instruction or platform policy is never repository
    content pretending to be approved - promotion must never talk it down
    to Priority 2 just because some unrelated manifest entry matches."""
    content = b"the exact approved bytes"
    promote_source(workspace, "skills/rules.txt", content)
    for higher in (TrustLevel.PLATFORM, TrustLevel.USER_INSTRUCTION):
        result = resolve_trust_level(workspace, "skills/rules.txt", content, default_level=higher)
        assert result == higher


def test_resolve_trust_level_raises_from_repository_or_external_equally(workspace):
    content = b"the exact approved bytes"
    promote_source(workspace, "skills/rules.txt", content)
    for lower in (TrustLevel.MILESTONE, TrustLevel.REPOSITORY, TrustLevel.EXTERNAL):
        result = resolve_trust_level(workspace, "skills/rules.txt", content, default_level=lower)
        assert result == TrustLevel.APPROVED_PROJECT_POLICY


def test_resolve_trust_level_never_mutates_the_manifest(workspace, monkeypatch):
    import kriya.policy.approved_sources as mod

    content = b"the exact approved bytes"
    promote_source(workspace, "skills/rules.txt", content)

    def boom(*args, **kwargs):
        raise AssertionError("resolve_trust_level must never call save_manifest")

    monkeypatch.setattr(mod, "save_manifest", boom)
    result = resolve_trust_level(workspace, "skills/rules.txt", content, default_level=TrustLevel.REPOSITORY)
    assert result == TrustLevel.APPROVED_PROJECT_POLICY


def test_approved_source_entry_is_frozen():
    import dataclasses
    entry = ApprovedSourceEntry(path="a.py", sha256="deadbeef")
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.sha256 = "other"

"""MA5.6: ContextPackage (kriya/workflow/context_package.py) - immutability,
hash-on-construction (both ContextItem.content_hash and
ContextPackage.package_hash), with_changes() producing a genuinely new
revision, and the omitted-context/provenance shape."""

import dataclasses

import pytest

from kriya.control.artifacts import ArtifactRecord
from kriya.control.contracts import ContractRecord, ContractState
from kriya.policy.trust import TrustLevel
from kriya.workflow.context_package import (
    CONTEXT_SOURCE_TYPES,
    ContextItem,
    ContextPackage,
    artifact_entry_from_record,
    build_context_package,
    contract_entry_from_record,
    make_context_item,
    make_omitted_entry,
)


def test_make_context_item_computes_a_real_content_hash():
    item = make_context_item(
        path="src/main.py", content="print(1)", reason="named_in_request",
        source_type="named_in_request", trust_level=TrustLevel.REPOSITORY,
    )
    import hashlib
    assert item.content_hash == hashlib.sha256(b"print(1)").hexdigest()


def test_make_context_item_normalizes_trust_level_to_its_string_value():
    item = make_context_item(
        path="x", content="y", reason="r", source_type="semantic_hit", trust_level=TrustLevel.EXTERNAL,
    )
    assert item.trust_level == "external"
    item2 = make_context_item(path="x", content="y", reason="r", source_type="semantic_hit", trust_level="repository")
    assert item2.trust_level == "repository"


def test_context_item_is_frozen():
    item = make_context_item(path="x", content="y", reason="r", source_type="semantic_hit", trust_level="repository")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.content = "z"


def test_context_source_types_cover_the_design_docs_own_vocabulary():
    assert set(CONTEXT_SOURCE_TYPES) == {
        "named_in_request", "stack_trace_frame", "lsp_reference", "graph_dependency",
        "semantic_hit", "contract_provider", "artifact_dependency", "carried_forward_acceptance",
    }


def test_make_omitted_entry_shape():
    entry = make_omitted_entry(path="src/big.py", rank=12, reason="over budget", estimated_tokens=4000)
    assert entry == {"path": "src/big.py", "rank": 12, "reason": "over budget", "estimated_tokens": 4000}


def test_build_context_package_computes_a_real_package_hash():
    pkg = build_context_package(conventions={"style": "pep8"}, token_count=42)
    assert pkg.package_hash != ""
    # Same content -> same hash
    pkg2 = build_context_package(conventions={"style": "pep8"}, token_count=42)
    assert pkg.package_hash == pkg2.package_hash


def test_package_hash_changes_when_content_changes():
    pkg1 = build_context_package(conventions={"style": "pep8"})
    pkg2 = build_context_package(conventions={"style": "black"})
    assert pkg1.package_hash != pkg2.package_hash


def test_context_package_is_frozen():
    pkg = build_context_package()
    with pytest.raises(dataclasses.FrozenInstanceError):
        pkg.token_count = 999


def test_with_changes_returns_a_new_package_with_a_new_hash():
    pkg = build_context_package(token_count=10)
    updated = pkg.with_changes(token_count=20)
    assert updated is not pkg
    assert updated.token_count == 20
    assert pkg.token_count == 10
    assert updated.package_hash != pkg.package_hash


def test_with_changes_ignores_a_caller_supplied_package_hash():
    """Passing package_hash directly must never let a caller fabricate a
    hash that doesn't match the real content - it's always recomputed."""
    pkg = build_context_package(token_count=10)
    updated = pkg.with_changes(token_count=10, package_hash="fabricated")
    assert updated.package_hash == pkg.package_hash
    assert updated.package_hash != "fabricated"


def test_relevant_files_round_trip_through_to_dict_from_dict():
    item = make_context_item(path="a.py", content="x=1", reason="named_in_request", source_type="named_in_request", trust_level="repository")
    pkg = build_context_package(relevant_files=(item,), token_count=5)
    reloaded = ContextPackage.from_dict(pkg.to_dict())
    assert len(reloaded.relevant_files) == 1
    assert reloaded.relevant_files[0].path == "a.py"
    assert reloaded.relevant_files[0].content_hash == item.content_hash


def test_omitted_is_carried_through_untouched():
    omitted = (make_omitted_entry("big.py", 1, "over budget", 5000),)
    pkg = build_context_package(omitted=omitted)
    assert pkg.omitted == omitted
    reloaded = ContextPackage.from_dict(pkg.to_dict())
    assert reloaded.omitted[0]["path"] == "big.py"


# --- CTX-001 P1 WP3: ContextItem's additive member/tier/revision fields ----

def test_context_item_new_fields_default_to_backward_compatible_values():
    """Every pre-P1 construction site (context_orchestrator.py, every
    existing test) constructs a ContextItem with no knowledge of the new
    fields at all - they must default to values that mean exactly what a
    pre-P1 ContextItem always implicitly meant: a whole-file unit, shown in
    full, not degraded."""
    item = make_context_item(
        path="x", content="y", reason="r", source_type="semantic_hit", trust_level="repository",
    )
    assert item.type_id is None
    assert item.member_id is None
    assert item.start_line is None
    assert item.end_line is None
    assert item.tier == "full"
    assert item.is_exact is True
    assert item.revision == ""
    assert item.omitted_regions is False


def test_make_context_item_accepts_new_keyword_only_fields():
    item = make_context_item(
        path="Impl.py", content="def calculate_total(): pass", reason="known_target_member_exact",
        source_type="named_in_request", trust_level=TrustLevel.REPOSITORY, score=0.9,
        type_id="StandardInvoiceCalculator", member_id="calculate_total",
        start_line=10, end_line=14, tier="member_exact", is_exact=True,
        revision="abc123", omitted_regions=False,
    )
    assert item.type_id == "StandardInvoiceCalculator"
    assert item.member_id == "calculate_total"
    assert item.start_line == 10
    assert item.end_line == 14
    assert item.tier == "member_exact"
    assert item.revision == "abc123"


def test_context_item_to_dict_from_dict_round_trips_new_fields():
    item = make_context_item(
        path="a.py", content="x=1", reason="r", source_type="semantic_hit", trust_level="repository",
        member_id="foo", tier="skeleton", is_exact=False, revision="deadbeef", omitted_regions=True,
        start_line=1, end_line=5,
    )
    reloaded = ContextItem.from_dict(item.to_dict())
    assert reloaded == item


def test_context_item_from_dict_defaults_new_fields_for_a_legacy_pre_p1_dict():
    """LEGACY_COMPATIBILITY: a ContextPackage serialized BEFORE this package
    (no WP3 keys at all in the dict) must still load correctly, not raise a
    KeyError."""
    legacy_dict = {
        "path": "old.py", "content": "print(1)", "reason": "named_in_request",
        "source_type": "named_in_request", "trust_level": "repository",
        "score": None, "content_hash": "irrelevant",
    }
    reloaded = ContextItem.from_dict(legacy_dict)
    assert reloaded.path == "old.py"
    assert reloaded.member_id is None
    assert reloaded.tier == "full"
    assert reloaded.is_exact is True
    assert reloaded.omitted_regions is False


def test_context_package_from_dict_loads_a_legacy_pre_p1_package():
    legacy_pkg_dict = {
        "conventions": {}, "relevant_files": [{
            "path": "old.py", "content": "x", "reason": "r", "source_type": "semantic_hit",
            "trust_level": "repository", "score": None, "content_hash": "h",
        }], "spec_slice": None, "carried_forward_criteria": [], "contract_entries": [],
        "artifact_entries": [], "baseline": None, "omitted": [], "token_count": 1,
        "package_hash": "whatever-a-pre-p1-run-computed",
    }
    reloaded = ContextPackage.from_dict(legacy_pkg_dict)
    assert len(reloaded.relevant_files) == 1
    assert reloaded.relevant_files[0].tier == "full"


def test_make_omitted_entry_with_member_id_adds_the_key():
    entry = make_omitted_entry(path="Big.py", rank=1, reason="body_elided", estimated_tokens=200, member_id="foo")
    assert entry["member_id"] == "foo"


def test_make_omitted_entry_without_member_id_matches_pre_p1_shape_exactly():
    """No new key at all when member_id isn't supplied - the pre-P1
    4-field dict shape is unchanged for every existing (non-member-aware)
    caller."""
    entry = make_omitted_entry(path="Big.py", rank=1, reason="over budget", estimated_tokens=200)
    assert entry == {"path": "Big.py", "rank": 1, "reason": "over budget", "estimated_tokens": 200}
    assert "member_id" not in entry


def test_contract_entry_from_record_and_artifact_entry_from_record_are_plain_dicts():
    contract = ContractRecord(id="M1:X", name="X", provider_milestone_id="M1", shape={}, state=ContractState.PROPOSED)
    artifact = ArtifactRecord(milestone_id="M1", ecosystem="maven", kind="library")
    contract_entry = contract_entry_from_record(contract)
    artifact_entry = artifact_entry_from_record(artifact)
    assert isinstance(contract_entry, dict)
    assert isinstance(artifact_entry, dict)
    assert contract_entry["id"] == "M1:X"
    assert artifact_entry["ecosystem"] == "maven"

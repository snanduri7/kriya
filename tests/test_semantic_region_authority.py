"""CORR-018-P1 (A3-bound slice): deterministic Java semantic-region authority.

Every test here is pure/offline - no LLM, no filesystem writes, no live
workflow run. The P10-regression test (test_p10_regression_*) is this
module's single most important test: it is the deterministic proof that the
production incident (an authorized method's body changed correctly, an
UNRELATED method's body changed silently alongside it, signatures/tests
stayed green) is now rejected.
"""
import hashlib

from kriya.workflow.semantic_region_authority import (
    REASON_IMPORT_UNAUTHORIZED,
    REASON_MEMBER_ADDED_UNAUTHORIZED,
    REASON_MEMBER_DELETED,
    REASON_REGION_UNAUTHORIZED,
    REASON_RESIDUAL_REGION_CHANGED,
    REASON_SCAN_AMBIGUOUS,
    REASON_SIGNATURE_UNAUTHORIZED,
    AuthorizedSemanticRegion,
    RegionType,
    build_file_snapshot,
    find_unauthorized_semantic_changes,
    stable_member_key,
)

RELPATH = "src/main/java/com/myapp/service/driver/DefaultDriverService.java"

BASELINE = """package com.myapp.service.driver;

import java.util.Optional;

public class DefaultDriverService implements DriverService {
    private final DriverRepository repository;

    public DefaultDriverService(DriverRepository repository) {
        this.repository = repository;
    }

    public DriverDO find(Long id) {
        return repository.findById(id);
    }

    public DriverDO create(DriverDO driver) {
        return repository.save(driver);
    }

    public void delete(Long id) {
        repository.deleteById(id);
    }

    public void updateLocation(Long id, String location) {
        repository.updateLocation(id, location);
    }
}
"""

TEST_BASELINE = """package com.myapp.service.driver;

public class DefaultDriverServiceTest {
    public void testFind() {
        assertNotNull(service.find(1L));
    }
}
"""

DELETE_KEY = stable_member_key(RELPATH, "DefaultDriverService", "method", "delete", ["Long"])
FIND_KEY = stable_member_key(RELPATH, "DefaultDriverService", "method", "find", ["Long"])
CREATE_KEY = stable_member_key(RELPATH, "DefaultDriverService", "method", "create", ["DriverDO"])
UPDATE_KEY = stable_member_key(RELPATH, "DefaultDriverService", "method", "updateLocation", ["Long", "String"])
CTOR_KEY = stable_member_key(RELPATH, "DefaultDriverService", "constructor", "DefaultDriverService", ["DriverRepository"])

DELETE_BODY_AUTH = AuthorizedSemanticRegion(relpath=RELPATH, region_type=RegionType.METHOD_BODY, member_key=DELETE_KEY)


def _with_authorized_delete_body_change(source: str = BASELINE) -> str:
    return source.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        if (repository.existsById(id)) {\n"
        "            repository.deleteById(id);\n        }\n    }\n",
    )


def _with_unrelated_find_change(source: str) -> str:
    return source.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        return repository.findById(id).orElse(null);\n    }\n",
    )


# =====================================================================
# Stable identity (1-4)
# =====================================================================

def test_stable_key_unchanged_across_whitespace_only_reformatting():
    shifted = BASELINE.replace("    public void delete", "    public void delete")  # no-op, control
    reformatted = "\n\n" + BASELINE  # every line shifted down by 2
    base_snap = build_file_snapshot(RELPATH, BASELINE)
    reformatted_snap = build_file_snapshot(RELPATH, reformatted)
    assert DELETE_KEY in base_snap.members
    assert DELETE_KEY in reformatted_snap.members
    assert base_snap.members[DELETE_KEY].body_hash == reformatted_snap.members[DELETE_KEY].body_hash


def test_overloads_produce_distinct_stable_keys():
    overloaded = BASELINE.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n\n"
        "    public DriverDO find(String code) {\n        return repository.findByCode(code);\n    }\n",
    )
    snap = build_file_snapshot(RELPATH, overloaded)
    find_long = stable_member_key(RELPATH, "DefaultDriverService", "method", "find", ["Long"])
    find_string = stable_member_key(RELPATH, "DefaultDriverService", "method", "find", ["String"])
    assert find_long != find_string
    assert find_long in snap.members and find_string in snap.members


def test_constructor_vs_method_distinct_key():
    snap = build_file_snapshot(RELPATH, BASELINE)
    assert CTOR_KEY in snap.members
    assert CTOR_KEY != FIND_KEY
    assert snap.members[CTOR_KEY].kind == "constructor"


def test_line_number_shift_does_not_change_identity():
    padded = "// leading comment\n// another one\n\n" + BASELINE
    padded_snap = build_file_snapshot(RELPATH, padded)
    base_snap = build_file_snapshot(RELPATH, BASELINE)
    assert DELETE_KEY in padded_snap.members
    assert padded_snap.members[DELETE_KEY].start_line != base_snap.members[DELETE_KEY].start_line
    assert padded_snap.members[DELETE_KEY].body_hash == base_snap.members[DELETE_KEY].body_hash


# =====================================================================
# Body authority (5-8)
# =====================================================================

def test_authorized_method_body_only_change_allowed():
    candidate = _with_authorized_delete_body_change()
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert violations == []


def test_authorized_method_plus_unrelated_method_change_rejected():
    """The P10-shaped defect at unit-test granularity: an authorized member
    changes correctly, but an unrelated member also changes."""
    candidate = _with_unrelated_find_change(_with_authorized_delete_body_change())
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    codes = {v.reason_code for v in violations}
    assert REASON_REGION_UNAUTHORIZED in codes
    assert any(v.member_key == FIND_KEY for v in violations)


def test_unrelated_constructor_change_rejected():
    candidate = BASELINE.replace(
        "    public DefaultDriverService(DriverRepository repository) {\n        this.repository = repository;\n    }\n",
        "    public DefaultDriverService(DriverRepository repository) {\n        this.repository = repository;\n        this.repository.warmup();\n    }\n",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert any(v.member_key == CTOR_KEY and v.reason_code == REASON_REGION_UNAUTHORIZED for v in violations)


def test_target_method_deleted_rejected():
    candidate = BASELINE.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n\n", "",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert any(v.member_key == DELETE_KEY and v.reason_code == REASON_MEMBER_DELETED for v in violations)


# =====================================================================
# Signature (9-11)
# =====================================================================

def test_body_only_authority_plus_signature_change_rejected():
    candidate = BASELINE.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public boolean delete(Long id) {\n        repository.deleteById(id);\n        return true;\n    }\n",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert any(v.member_key == DELETE_KEY and v.reason_code == REASON_SIGNATURE_UNAUTHORIZED for v in violations)


def test_explicit_signature_authority_allows_intended_signature_change():
    candidate = BASELINE.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public boolean delete(Long id) {\n        repository.deleteById(id);\n        return true;\n    }\n",
    )
    sig_auth = AuthorizedSemanticRegion(relpath=RELPATH, region_type=RegionType.METHOD_SIGNATURE, member_key=DELETE_KEY)
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [sig_auth])
    assert violations == []


def test_signature_change_plus_unrelated_other_member_change_rejected():
    candidate = _with_unrelated_find_change(BASELINE.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public boolean delete(Long id) {\n        repository.deleteById(id);\n        return true;\n    }\n",
    ))
    sig_auth = AuthorizedSemanticRegion(relpath=RELPATH, region_type=RegionType.METHOD_SIGNATURE, member_key=DELETE_KEY)
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [sig_auth])
    assert any(v.member_key == FIND_KEY for v in violations)


# =====================================================================
# Helpers (12-14)
# =====================================================================

_WITH_HELPER = BASELINE.replace(
    "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
    "    public void delete(Long id) {\n        assertExists(id);\n        repository.deleteById(id);\n    }\n\n"
    "    private void assertExists(Long id) {\n        if (!repository.existsById(id)) {\n"
    "            throw new IllegalStateException();\n        }\n    }\n",
)
HELPER_KEY = stable_member_key(RELPATH, "DefaultDriverService", "method", "assertExists", ["Long"])


def test_new_helper_without_method_add_rejected():
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: _WITH_HELPER}, [DELETE_BODY_AUTH])
    assert any(v.member_key == HELPER_KEY and v.reason_code == REASON_MEMBER_ADDED_UNAUTHORIZED for v in violations)


def test_explicit_helper_authority_allowed():
    helper_auth = AuthorizedSemanticRegion(relpath=RELPATH, region_type=RegionType.METHOD_ADD, member_key=HELPER_KEY)
    violations = find_unauthorized_semantic_changes(
        {RELPATH: BASELINE}, {RELPATH: _WITH_HELPER}, [DELETE_BODY_AUTH, helper_auth],
    )
    assert violations == []


def test_helper_addition_plus_unrelated_second_helper_rejected():
    with_two_helpers = _WITH_HELPER.replace(
        "    private void assertExists(Long id) {\n        if (!repository.existsById(id)) {\n"
        "            throw new IllegalStateException();\n        }\n    }\n",
        "    private void assertExists(Long id) {\n        if (!repository.existsById(id)) {\n"
        "            throw new IllegalStateException();\n        }\n    }\n\n"
        "    private void auditDeletion(Long id) {\n        System.out.println(id);\n    }\n",
    )
    helper_auth = AuthorizedSemanticRegion(relpath=RELPATH, region_type=RegionType.METHOD_ADD, member_key=HELPER_KEY)
    violations = find_unauthorized_semantic_changes(
        {RELPATH: BASELINE}, {RELPATH: with_two_helpers}, [DELETE_BODY_AUTH, helper_auth],
    )
    audit_key = stable_member_key(RELPATH, "DefaultDriverService", "method", "auditDeletion", ["Long"])
    assert any(v.member_key == audit_key and v.reason_code == REASON_MEMBER_ADDED_UNAUTHORIZED for v in violations)


# =====================================================================
# Imports (15-19)
# =====================================================================

IMPORTS_AUTH = AuthorizedSemanticRegion(relpath=RELPATH, region_type=RegionType.IMPORTS)


def test_required_import_referenced_only_by_authorized_region_allowed():
    candidate = BASELINE.replace(
        "import java.util.Optional;\n", "import java.util.Optional;\nimport java.util.Objects;\n",
    ).replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        Objects.requireNonNull(id);\n        repository.deleteById(id);\n    }\n",
    )
    violations = find_unauthorized_semantic_changes(
        {RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH, IMPORTS_AUTH],
    )
    assert violations == []


def test_unrelated_import_addition_rejected():
    candidate = BASELINE.replace(
        "import java.util.Optional;\n",
        "import java.util.Optional;\nimport java.util.concurrent.ExecutorService;\n",
    )
    candidate = _with_authorized_delete_body_change(candidate)
    violations = find_unauthorized_semantic_changes(
        {RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH, IMPORTS_AUTH],
    )
    assert any(v.reason_code == REASON_IMPORT_UNAUTHORIZED for v in violations)


def test_wildcard_import_addition_rejected():
    candidate = BASELINE.replace("import java.util.Optional;\n", "import java.util.Optional;\nimport java.util.*;\n")
    candidate = _with_authorized_delete_body_change(candidate)
    violations = find_unauthorized_semantic_changes(
        {RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH, IMPORTS_AUTH],
    )
    assert any(v.reason_code == REASON_IMPORT_UNAUTHORIZED for v in violations)


def test_static_import_used_in_authorized_region_allowed():
    candidate = BASELINE.replace(
        "import java.util.Optional;\n",
        "import java.util.Optional;\nimport static java.util.Objects.requireNonNull;\n",
    ).replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        requireNonNull(id);\n        repository.deleteById(id);\n    }\n",
    )
    violations = find_unauthorized_semantic_changes(
        {RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH, IMPORTS_AUTH],
    )
    assert violations == []


def test_removed_import_still_used_elsewhere_rejected():
    with_extra_use = BASELINE.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        Optional<DriverDO> o = Optional.empty();\n"
        "        return repository.findById(id);\n    }\n",
    )
    candidate = with_extra_use.replace("import java.util.Optional;\n", "")
    candidate = _with_authorized_delete_body_change(candidate)
    violations = find_unauthorized_semantic_changes(
        {RELPATH: with_extra_use}, {RELPATH: candidate}, [DELETE_BODY_AUTH, IMPORTS_AUTH],
    )
    assert any(v.reason_code == REASON_IMPORT_UNAUTHORIZED for v in violations)


# =====================================================================
# Residual region (20-23)
# =====================================================================

def test_field_modification_rejected():
    candidate = _with_authorized_delete_body_change(BASELINE).replace(
        "    private final DriverRepository repository;\n",
        "    private final DriverRepository repository;\n    private boolean cacheEnabled = false;\n",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert any(v.reason_code == REASON_RESIDUAL_REGION_CHANGED for v in violations)


def test_class_annotation_modifier_change_rejected():
    candidate = _with_authorized_delete_body_change(BASELINE).replace(
        "public class DefaultDriverService implements DriverService {",
        "@Deprecated\npublic class DefaultDriverService implements DriverService {",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert any(v.reason_code == REASON_RESIDUAL_REGION_CHANGED for v in violations)


def test_formatting_comment_only_churn_outside_target_allowed():
    candidate = _with_authorized_delete_body_change(BASELINE).replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        // no behavior change here\n        return repository.findById(id);\n    }\n",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert violations == []


def test_string_literal_change_inside_authorized_region_still_material():
    """Part 6: a changed string literal is NOT normalized away, even inside
    the authorized region - it's just authorized (allowed), not invisible."""
    candidate = BASELINE.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        '    public void delete(Long id) {\n        System.out.println("deleting");\n        repository.deleteById(id);\n    }\n',
    )
    base_snap = build_file_snapshot(RELPATH, BASELINE)
    cand_snap = build_file_snapshot(RELPATH, candidate)
    assert base_snap.members[DELETE_KEY].body_hash != cand_snap.members[DELETE_KEY].body_hash
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert violations == []  # authorized region, string literal change included and fine


# =====================================================================
# Tests (24-27)
# =====================================================================

TEST_RELPATH = "src/test/java/com/myapp/service/driver/DefaultDriverServiceTest.java"


def test_explicitly_authorized_new_test_method_allowed():
    candidate = TEST_BASELINE.replace(
        "    public void testFind() {\n        assertNotNull(service.find(1L));\n    }\n",
        "    public void testFind() {\n        assertNotNull(service.find(1L));\n    }\n\n"
        "    public void testDelete() {\n        service.delete(1L);\n    }\n",
    )
    new_method_key = stable_member_key(TEST_RELPATH, "DefaultDriverServiceTest", "method", "testDelete", [])
    test_add_auth = AuthorizedSemanticRegion(relpath=TEST_RELPATH, region_type=RegionType.TEST_METHOD_ADD, member_key=new_method_key)
    violations = find_unauthorized_semantic_changes({TEST_RELPATH: TEST_BASELINE}, {TEST_RELPATH: candidate}, [test_add_auth])
    assert violations == []


def test_existing_unrelated_test_method_changed_rejected():
    candidate = TEST_BASELINE.replace(
        "    public void testFind() {\n        assertNotNull(service.find(1L));\n    }\n",
        "    public void testFind() {\n        assertNull(service.find(1L));\n    }\n",
    )
    real_new_key = stable_member_key(TEST_RELPATH, "DefaultDriverServiceTest", "method", "testDelete", [])
    test_add_auth = AuthorizedSemanticRegion(relpath=TEST_RELPATH, region_type=RegionType.TEST_METHOD_ADD, member_key=real_new_key)
    violations = find_unauthorized_semantic_changes({TEST_RELPATH: TEST_BASELINE}, {TEST_RELPATH: candidate}, [test_add_auth])
    find_test_key = stable_member_key(TEST_RELPATH, "DefaultDriverServiceTest", "method", "testFind", [])
    assert any(v.member_key == find_test_key and v.reason_code == REASON_REGION_UNAUTHORIZED for v in violations)


def test_unauthorized_new_test_method_rejected():
    candidate = TEST_BASELINE.replace(
        "    public void testFind() {\n        assertNotNull(service.find(1L));\n    }\n",
        "    public void testFind() {\n        assertNotNull(service.find(1L));\n    }\n\n"
        "    public void testDelete() {\n        service.delete(1L);\n    }\n",
    )
    violations = find_unauthorized_semantic_changes({TEST_RELPATH: TEST_BASELINE}, {TEST_RELPATH: candidate}, [])
    assert violations == []  # no authorized_regions at all for this relpath -> no-op, untouched by this guard


def test_explicitly_authorized_new_test_file_allowed():
    new_test_file_content = (
        "package com.myapp.service.driver;\n\n"
        "public class NewFeatureTest {\n    public void testNewFeature() {\n    }\n}\n"
    )
    new_test_relpath = "src/test/java/com/myapp/service/driver/NewFeatureTest.java"
    file_add_auth = AuthorizedSemanticRegion(relpath=new_test_relpath, region_type=RegionType.TEST_FILE_ADD)
    violations = find_unauthorized_semantic_changes({}, {new_test_relpath: new_test_file_content}, [file_add_auth])
    assert violations == []


def test_unauthorized_new_file_rejected():
    new_test_relpath = "src/test/java/com/myapp/service/driver/NewFeatureTest.java"
    new_test_file_content = "package x;\npublic class NewFeatureTest {\n}\n"
    other_auth = AuthorizedSemanticRegion(relpath=new_test_relpath, region_type=RegionType.METHOD_BODY, member_key="irrelevant")
    violations = find_unauthorized_semantic_changes({}, {new_test_relpath: new_test_file_content}, [other_auth])
    assert any(v.reason_code == REASON_MEMBER_ADDED_UNAUTHORIZED for v in violations)


# =====================================================================
# Ambiguity (28-30)
# =====================================================================

def test_duplicate_stable_key_treated_as_ambiguous():
    """Two overloads whose parameter TYPES normalize identically (e.g.
    differ only in a formatting quirk our normalizer collapses) collide -
    the snapshot must mark itself ambiguous rather than silently keeping
    only one."""
    colliding_source = BASELINE.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long  id) {\n        return repository.findById(id);\n    }\n\n"
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
    )
    snap = build_file_snapshot(RELPATH, colliding_source)
    assert snap.ambiguous is True


def test_ambiguous_candidate_scan_rejects_entire_file():
    colliding_source = BASELINE.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long  id) {\n        return repository.findById(id);\n    }\n\n"
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
    )
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: colliding_source}, [DELETE_BODY_AUTH])
    assert len(violations) == 1
    assert violations[0].reason_code == REASON_SCAN_AMBIGUOUS


def test_scan_failure_fails_closed_not_silently():
    import kriya.workflow.semantic_region_authority as sra
    original = sra.extract_java_members

    def _boom(*a, **k):
        raise RuntimeError("simulated scanner crash")

    sra.extract_java_members = _boom
    try:
        candidate = _with_authorized_delete_body_change()
        violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    finally:
        sra.extract_java_members = original
    assert len(violations) == 1
    assert violations[0].reason_code == REASON_SCAN_AMBIGUOUS


# =====================================================================
# Recovery invariance (31)
# =====================================================================

def test_same_authorized_regions_reused_across_retry_still_rejects_broader_change():
    """Simulates a retry attempt that widens scope beyond attempt 1's own
    authority - the SAME authorized_regions list is reused (as the real
    integration does - see attempt.py/workflow.py wiring, both derive the
    set once, never per-attempt), so a recovery candidate touching create()
    is rejected exactly like a first attempt would be."""
    attempt1_candidate = _with_authorized_delete_body_change()
    v1 = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: attempt1_candidate}, [DELETE_BODY_AUTH])
    assert v1 == []

    recovery_candidate = attempt1_candidate.replace(
        "    public DriverDO create(DriverDO driver) {\n        return repository.save(driver);\n    }\n",
        "    public DriverDO create(DriverDO driver) {\n        return repository.save(driver != null ? driver : new DriverDO());\n    }\n",
    )
    v2 = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: recovery_candidate}, [DELETE_BODY_AUTH])
    assert any(v.member_key == CREATE_KEY and v.reason_code == REASON_REGION_UNAUTHORIZED for v in v2)


# =====================================================================
# P10 regression (32) - the essential test
# =====================================================================

def test_p10_regression_unrelated_same_file_body_change_is_rejected():
    """Recreates the essential P10 shape: the required target member change
    is made correctly; an UNRELATED member in the SAME file also changes;
    public signatures are preserved throughout (this test's candidate would
    pass find_brownfield_public_api_changes() cleanly - no signature is
    touched at all). Before CORR-018-P1, nothing in Kriya detected this.
    This test is the deterministic proof that the guard now does."""
    candidate = _with_unrelated_find_change(_with_authorized_delete_body_change())

    base_snap = build_file_snapshot(RELPATH, BASELINE)
    cand_snap = build_file_snapshot(RELPATH, candidate)
    assert base_snap.members[DELETE_KEY].normalized_signature == cand_snap.members[DELETE_KEY].normalized_signature
    assert base_snap.members[FIND_KEY].normalized_signature == cand_snap.members[FIND_KEY].normalized_signature

    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, [DELETE_BODY_AUTH])
    assert violations != [], "P10-shaped unrelated body change must be REJECTED, not silently accepted"
    assert any(v.member_key == FIND_KEY and v.reason_code == REASON_REGION_UNAUTHORIZED for v in violations)
    assert not any(v.member_key == DELETE_KEY for v in violations)  # the authorized change itself is clean


# =====================================================================
# Existing A1/A2 behavior unchanged (33-36)
# =====================================================================

def test_no_authorized_regions_is_a_full_noop():
    """Every existing caller today (no A3 wiring exists yet) passes nothing,
    which must be byte-for-byte today's behavior: zero violations, no
    matter how much the candidate changed."""
    candidate = _with_unrelated_find_change(_with_authorized_delete_body_change()).replace(
        "    private final DriverRepository repository;\n",
        "    private final DriverRepository repository;\n    private boolean x;\n",
    )
    assert find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: candidate}, []) == []


def test_java_member_inventory_extraction_itself_unchanged():
    """CORR-018-P1 reuses extract_java_members() read-only - never patches
    or wraps it in a way that changes its own return shape for other
    callers (A1's review path in particular)."""
    from kriya.analyzer.java_members import extract_java_members
    members = extract_java_members(BASELINE)
    names = {m.name for m in members}
    assert names == {"DefaultDriverService", "find", "create", "delete", "updateLocation"}


def test_unchanged_file_produces_no_violations_even_with_regions_declared():
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {RELPATH: BASELINE}, [DELETE_BODY_AUTH])
    assert violations == []


def test_file_not_present_in_candidate_is_skipped_not_flagged():
    """A file named in authorized_regions that the candidate never touched
    at all (final_contents has no entry for it) must not be flagged - there
    is nothing to compare, and raising here would reject candidates that
    correctly left an authorized-but-untouched file alone."""
    violations = find_unauthorized_semantic_changes({RELPATH: BASELINE}, {}, [DELETE_BODY_AUTH])
    assert violations == []

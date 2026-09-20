"""A3-P0: durable binding between A2 ProposedModification and CORR-018-P1's
AuthorizedSemanticRegion[] enforcement path.

Every test here is read-only against real files under pytest's own `tmp_path`
- no live LLM, no generation workflow, no `.kriya/proposals` persistence
(explicitly out of scope for this slice). The integration test at the bottom
(`test_full_chain_...`) is this module's most important test: it proves the
entire A1 finding -> A2 proposal -> durable binding -> verification ->
CORR-018 translation -> enforcement chain is compatible end to end.
"""
import hashlib
import os
import shutil

import pytest

from kriya.analyzer.java_members import extract_java_members
from kriya.workflow.review_context import (
    CONFIDENCE_STRONG_STATIC_INDICATION,
    StructuredFinding,
    adjudicate_findings,
    build_member_evidence_ids,
    build_proposed_modification,
    format_proposed_modification,
)
from kriya.workflow.proposal_binding import (
    REASON_EVIDENCE_CHANGED,
    REASON_EVIDENCE_FILE_STALE,
    REASON_TARGET_FILE_STALE,
    REASON_TARGET_MEMBER_MISSING,
    REASON_WRONG_WORKSPACE,
    EvidenceBinding,
    bind_evidence,
    proposal_to_authorized_semantic_regions,
    sha256_file,
    verify_proposal_bindings,
)
from kriya.workflow.semantic_region_authority import (
    RegionType,
    find_unauthorized_semantic_changes,
    stable_member_key,
)

RELPATH = "src/main/java/com/myapp/service/driver/DefaultDriverService.java"

TARGET_SRC = (
    "package com.myapp.service.driver;\n\n"
    "public class DefaultDriverService implements DriverService {\n"
    "    private final DriverRepository repository;\n\n"
    "    public DefaultDriverService(DriverRepository repository) {\n"
    "        this.repository = repository;\n"
    "    }\n\n"
    "    public DriverDO find(Long id) {\n"
    "        return repository.findById(id);\n"
    "    }\n\n"
    "    public DriverDO find(String code) {\n"
    "        return repository.findByCode(code);\n"
    "    }\n\n"
    "    public void delete(Long id) {\n"
    "        repository.deleteById(id);\n"
    "    }\n"
    "}\n"
)

RELATED_SRC = "package com.myapp.service.driver;\npublic interface DriverService {\n}\n"
RELATED_RELPATH = "src/main/java/com/myapp/service/driver/DriverService.java"


def _write_target(root):
    abs_path = os.path.join(root, RELPATH)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w") as fh:
        fh.write(TARGET_SRC)
    return abs_path


def _write_related(root):
    abs_path = os.path.join(root, RELATED_RELPATH)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w") as fh:
        fh.write(RELATED_SRC)
    return abs_path


def _member_ids_and_delete_key():
    members = extract_java_members(TARGET_SRC)
    member_ids = build_member_evidence_ids(members)
    delete_mid = next(mid for mid, m in member_ids.items() if m.name == "delete")
    return member_ids, delete_mid


def _finding(member_id, condition_ids, consequence_ids=()):
    return StructuredFinding(
        finding_id="F1", title="t", member_id=member_id,
        requested_confidence=CONFIDENCE_STRONG_STATIC_INDICATION,
        condition_evidence_ids=condition_ids, consequence_evidence_ids=consequence_ids,
        runtime_dependency_declared=False, explanation="",
        recommendation="Check repository.existsById(id) before deleting.",
    )


def _built_proposal(workspace_root):
    member_ids, delete_mid = _member_ids_and_delete_key()
    f = _finding(delete_mid, (delete_mid,))
    [adj] = adjudicate_findings([f], member_ids, {})
    return build_proposed_modification("F1", [adj], member_ids, {}, RELPATH, workspace_root=workspace_root)


# =====================================================================
# Evidence identity (1-5)
# =====================================================================

def test_display_id_does_not_affect_durable_identity(tmp_path):
    """M-however-many the model happens to assign is irrelevant to the
    durable key - only the underlying member's real signature is."""
    _write_target(str(tmp_path))
    member_ids, delete_mid = _member_ids_and_delete_key()
    # Relabel every display id (simulating a different invocation's own
    # M#/R# numbering) - the durable stable_structural_key must be identical.
    relabeled = {f"M{100+i}": m for i, m in enumerate(member_ids.values())}
    relabeled_delete_mid = next(mid for mid, m in relabeled.items() if m.name == "delete")
    f = _finding(relabeled_delete_mid, (relabeled_delete_mid,))
    [adj] = adjudicate_findings([f], relabeled, {})
    proposal = build_proposed_modification("F1", [adj], relabeled, {}, RELPATH, workspace_root=str(tmp_path))
    original_key = stable_member_key(RELPATH, "DefaultDriverService", "method", "delete", ["Long"])
    assert proposal.target_member_key == original_key
    assert proposal.evidence_bindings[0].stable_structural_key == original_key


def test_line_number_shift_same_structural_key(tmp_path):
    padded_src = "// leading\n// comment\n\n" + TARGET_SRC
    abs_path = os.path.join(str(tmp_path), RELPATH)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w") as fh:
        fh.write(padded_src)

    members = extract_java_members(padded_src)
    member_ids = build_member_evidence_ids(members)
    delete_mid = next(mid for mid, m in member_ids.items() if m.name == "delete")
    f = _finding(delete_mid, (delete_mid,))
    [adj] = adjudicate_findings([f], member_ids, {})
    proposal = build_proposed_modification("F1", [adj], member_ids, {}, RELPATH, workspace_root=str(tmp_path))

    expected_key = stable_member_key(RELPATH, "DefaultDriverService", "method", "delete", ["Long"])
    assert proposal.target_member_key == expected_key


def test_whitespace_comment_only_change_same_normalized_hash(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    original_hash = proposal.evidence_bindings[0].normalized_content_hash

    reformatted = TARGET_SRC.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        // no behavior change\n        repository.deleteById(id);\n    }\n",
    )
    abs_path = os.path.join(str(tmp_path), RELPATH)
    with open(abs_path, "w") as fh:
        fh.write(reformatted)
    proposal2 = _built_proposal(str(tmp_path))
    assert proposal2.evidence_bindings[0].normalized_content_hash == original_hash
    # restore
    with open(abs_path, "w") as fh:
        fh.write(TARGET_SRC)


def test_body_semantic_change_different_normalized_hash(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    original_hash = proposal.evidence_bindings[0].normalized_content_hash

    changed = TARGET_SRC.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        repository.softDeleteById(id);\n    }\n",
    )
    abs_path = os.path.join(str(tmp_path), RELPATH)
    with open(abs_path, "w") as fh:
        fh.write(changed)
    proposal2 = _built_proposal(str(tmp_path))
    assert proposal2.evidence_bindings[0].normalized_content_hash != original_hash
    with open(abs_path, "w") as fh:
        fh.write(TARGET_SRC)


def test_overloads_bind_separately(tmp_path):
    _write_target(str(tmp_path))
    members = extract_java_members(TARGET_SRC)
    member_ids = build_member_evidence_ids(members)
    find_long_mid = next(mid for mid, m in member_ids.items() if m.name == "find" and m.parameter_types == ("Long",))
    find_string_mid = next(mid for mid, m in member_ids.items() if m.name == "find" and m.parameter_types == ("String",))
    eb_long = bind_evidence(find_long_mid, member_ids, {}, str(tmp_path), RELPATH)
    eb_string = bind_evidence(find_string_mid, member_ids, {}, str(tmp_path), RELPATH)
    assert eb_long.stable_structural_key != eb_string.stable_structural_key


# =====================================================================
# File hashes (6-9)
# =====================================================================

def test_target_file_hash_stable_for_identical_bytes(tmp_path):
    abs_path = _write_target(str(tmp_path))
    assert sha256_file(abs_path) == sha256_file(abs_path)


def test_one_byte_target_change_different_hash(tmp_path):
    abs_path = _write_target(str(tmp_path))
    h1 = sha256_file(abs_path)
    with open(abs_path, "a") as fh:
        fh.write("x")
    h2 = sha256_file(abs_path)
    assert h1 != h2


def test_evidence_file_hash_change_detected(tmp_path):
    _write_target(str(tmp_path))
    related_abs = _write_related(str(tmp_path))
    from kriya.workflow.review_context import RelatedFile
    relation_ids = {"R1": RelatedFile(relpath=RELATED_RELPATH, relation="implements", detail="implements DriverService")}
    eb1 = bind_evidence("R1", {}, relation_ids, str(tmp_path), RELPATH)
    with open(related_abs, "a") as fh:
        fh.write("// changed\n")
    eb2 = bind_evidence("R1", {}, relation_ids, str(tmp_path), RELPATH)
    assert eb1.file_content_sha256 != eb2.file_content_sha256


def test_line_ending_change_changes_exact_file_hash(tmp_path):
    abs_path = _write_target(str(tmp_path))
    h1 = sha256_file(abs_path)
    with open(abs_path, "wb") as fh:
        fh.write(TARGET_SRC.replace("\n", "\r\n").encode("utf-8"))
    h2 = sha256_file(abs_path)
    assert h1 != h2


# =====================================================================
# Dirty workspace (10-13) - the critical A3-D1 gap test
# =====================================================================

def test_dirty_workspace_content_change_detected_by_file_hash_not_fooled_by_coarse_fingerprint(tmp_path):
    """The load-bearing A3-P0 test: two different dirty-tree CONTENTS must
    be distinguishable even though compute_workspace_fingerprint()'s own
    HEAD+dirty-boolean signal cannot tell them apart - the per-file SHA-256
    binding is what actually catches this."""
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))  # proposal created against dirty state X

    result_unchanged = verify_proposal_bindings(proposal, str(tmp_path))
    assert result_unchanged.ok is True

    # Mutate to dirty state Y, without committing anything (no git repo at
    # all here, so compute_workspace_fingerprint() returns None either way -
    # the worst case: zero coarse signal, file-hash binding is the ONLY
    # protection, and it must still catch this.)
    abs_path = os.path.join(str(tmp_path), RELPATH)
    with open(abs_path, "a") as fh:
        fh.write("    // dirty state Y - added after the proposal was built\n")

    result_stale = verify_proposal_bindings(proposal, str(tmp_path))
    assert result_stale.ok is False
    assert REASON_TARGET_FILE_STALE in result_stale.reason_codes


def test_workspace_fingerprint_none_for_non_git_dir_is_the_documented_weak_case(tmp_path):
    """Confirms in code, not just by inspection, that a non-git workspace's
    fingerprint really is None - the exact coarse-signal weakness Part 5
    describes; the file-hash binding above is what actually protects a
    proposal in this case, not the fingerprint."""
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    assert proposal.workspace_fingerprint is None


# =====================================================================
# Workspace identity (14-15)
# =====================================================================

def test_proposal_verified_in_same_repo_passes(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    assert verify_proposal_bindings(proposal, str(tmp_path)).ok is True


def test_same_files_different_workspace_root_rejected(tmp_path, tmp_path_factory):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))

    other_root = tmp_path_factory.mktemp("other_workspace")
    other_abs = os.path.join(str(other_root), RELPATH)
    os.makedirs(os.path.dirname(other_abs), exist_ok=True)
    shutil.copy(os.path.join(str(tmp_path), RELPATH), other_abs)

    result = verify_proposal_bindings(proposal, str(other_root))
    assert result.ok is False
    assert REASON_WRONG_WORKSPACE in result.reason_codes


# =====================================================================
# Target member (16-18)
# =====================================================================

def test_target_stable_key_resolves_passes(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    result = verify_proposal_bindings(proposal, str(tmp_path))
    assert REASON_TARGET_MEMBER_MISSING not in result.reason_codes


def test_target_member_removed_rejected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    without_delete = TARGET_SRC.replace(
        "\n    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n", "",
    )
    with open(abs_path, "w") as fh:
        fh.write(without_delete)
    result = verify_proposal_bindings(proposal, str(tmp_path))
    assert result.ok is False
    assert REASON_TARGET_MEMBER_MISSING in result.reason_codes


def test_overload_ambiguity_never_silently_binds_wrong_member(tmp_path):
    """bind_evidence()/build_proposed_modification() always key by the
    member's OWN real (kind, name, parameter_types) - never by name alone -
    so a same-named overload can never be silently substituted."""
    _write_target(str(tmp_path))
    members = extract_java_members(TARGET_SRC)
    member_ids = build_member_evidence_ids(members)
    find_long_mid = next(mid for mid, m in member_ids.items() if m.name == "find" and m.parameter_types == ("Long",))
    eb = bind_evidence(find_long_mid, member_ids, {}, str(tmp_path), RELPATH)
    assert "(Long)" in eb.stable_structural_key
    assert "(String)" not in eb.stable_structural_key


# =====================================================================
# Evidence (19-22)
# =====================================================================

def test_evidence_member_unchanged_passes(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    result = verify_proposal_bindings(proposal, str(tmp_path))
    assert REASON_EVIDENCE_CHANGED not in result.reason_codes
    assert REASON_EVIDENCE_FILE_STALE not in result.reason_codes


def test_evidence_member_semantically_changed_rejected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    changed = TARGET_SRC.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        repository.softDeleteById(id);\n    }\n",
    )
    with open(abs_path, "w") as fh:
        fh.write(changed)
    result = verify_proposal_bindings(proposal, str(tmp_path))
    assert result.ok is False
    # both the target-file hash AND the evidence file hash cover this file
    assert REASON_TARGET_FILE_STALE in result.reason_codes or REASON_EVIDENCE_FILE_STALE in result.reason_codes


def test_evidence_file_changed_elsewhere_only_stale_due_to_exact_file_hash(tmp_path):
    """A change anywhere in the evidence file (even outside the specific
    evidence member) invalidates the file-hash binding - exact reviewed
    state, not just the cited member's own normalized content."""
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    unrelated_change = TARGET_SRC.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        return repository.findById(id).orElse(null);\n    }\n",
    )
    with open(abs_path, "w") as fh:
        fh.write(unrelated_change)
    result = verify_proposal_bindings(proposal, str(tmp_path))
    assert result.ok is False
    assert REASON_TARGET_FILE_STALE in result.reason_codes  # target IS the evidence file here


def test_evidence_member_missing_rejected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    without_delete = TARGET_SRC.replace(
        "\n    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n", "",
    )
    with open(abs_path, "w") as fh:
        fh.write(without_delete)
    result = verify_proposal_bindings(proposal, str(tmp_path))
    assert result.ok is False  # target member missing is itself sufficient rejection


# =====================================================================
# Proposal translation (23-27)
# =====================================================================

def test_method_body_proposal_translates_to_method_body(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    regions = proposal_to_authorized_semantic_regions(proposal)
    body_regions = [r for r in regions if r.region_type in (RegionType.METHOD_BODY, RegionType.CONSTRUCTOR_BODY)]
    assert len(body_regions) == 1
    assert body_regions[0].region_type == RegionType.METHOD_BODY
    assert body_regions[0].member_key == proposal.target_member_key


def test_constructor_body_proposal_translates_to_constructor_body(tmp_path):
    _write_target(str(tmp_path))
    members = extract_java_members(TARGET_SRC)
    member_ids = build_member_evidence_ids(members)
    ctor_mid = next(mid for mid, m in member_ids.items() if m.kind == "constructor")
    f = _finding(ctor_mid, (ctor_mid,))
    [adj] = adjudicate_findings([f], member_ids, {})
    proposal = build_proposed_modification("F1", [adj], member_ids, {}, RELPATH, workspace_root=str(tmp_path))
    regions = proposal_to_authorized_semantic_regions(proposal)
    body_regions = [r for r in regions if r.region_type in (RegionType.METHOD_BODY, RegionType.CONSTRUCTOR_BODY)]
    assert body_regions[0].region_type == RegionType.CONSTRUCTOR_BODY


def test_translation_always_includes_imports_region(tmp_path):
    """Documented, bounded limitation: A2's current shape only ever
    expresses body-level authority; IMPORTS is always granted alongside it
    (safe by construction - CORR-018-P1 still requires an actual reference)."""
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    regions = proposal_to_authorized_semantic_regions(proposal)
    assert any(r.region_type == RegionType.IMPORTS for r in regions)


def test_unbound_proposal_fails_closed_not_whole_file(tmp_path):
    """A finding not tied to any specific deterministic member (member_id is
    None, grounded only via relation evidence) has no durable
    target_member_key at all - translation must refuse outright, never fall
    back to WHOLE_FILE (there is no such region type - see RegionType)."""
    _write_target(str(tmp_path))
    _write_related(str(tmp_path))
    from kriya.workflow.review_context import RelatedFile
    relation_ids = {"R1": RelatedFile(relpath=RELATED_RELPATH, relation="implements", detail="implements DriverService")}
    memberless_finding = StructuredFinding(
        finding_id="F1", title="t", member_id=None,
        requested_confidence=CONFIDENCE_STRONG_STATIC_INDICATION,
        condition_evidence_ids=("R1",), consequence_evidence_ids=(),
        runtime_dependency_declared=False, explanation="",
        recommendation="Some class-level recommendation.",
    )
    member_ids, _ = _member_ids_and_delete_key()
    [adj] = adjudicate_findings([memberless_finding], member_ids, relation_ids)
    unbound = build_proposed_modification("F1", [adj], member_ids, relation_ids, RELPATH)  # no workspace_root either
    assert unbound.target_member_key is None
    with pytest.raises(ValueError):
        proposal_to_authorized_semantic_regions(unbound)


def test_translation_requires_no_planner_or_candidate_data(tmp_path):
    """Pure function signature proof: proposal_to_authorized_semantic_regions()
    takes only a proposal - no plan, no candidate, no LLM client."""
    import inspect
    params = list(inspect.signature(proposal_to_authorized_semantic_regions).parameters)
    assert params == ["proposal"]


# =====================================================================
# Zero write (28-30)
# =====================================================================

def _hash_tree(root):
    h = hashlib.sha256()
    for dirpath, _, filenames in sorted(os.walk(root)):
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            h.update(p.encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def test_build_bound_proposal_repo_byte_identical(tmp_path):
    _write_target(str(tmp_path))
    before = _hash_tree(str(tmp_path))
    _built_proposal(str(tmp_path))
    assert _hash_tree(str(tmp_path)) == before


def test_verify_proposal_repo_byte_identical(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    before = _hash_tree(str(tmp_path))
    verify_proposal_bindings(proposal, str(tmp_path))
    assert _hash_tree(str(tmp_path)) == before


def test_translate_to_regions_repo_byte_identical(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    before = _hash_tree(str(tmp_path))
    proposal_to_authorized_semantic_regions(proposal)
    assert _hash_tree(str(tmp_path)) == before


def test_dynamic_zero_write_proof_write_capable_components_never_invoked(tmp_path):
    from unittest.mock import patch
    _write_target(str(tmp_path))

    def _raise(*a, **k):
        raise AssertionError("A3-P0 zero-write violation: a write-capable component was invoked")

    with patch("kriya.policy.filesystem.AuthorizedFileWriter.commit_file", side_effect=_raise), \
         patch("kriya.agents.agent.DeveloperAgent.run_generation", side_effect=_raise), \
         patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", side_effect=_raise), \
         patch("kriya.workflow.checkpoint.save_checkpoint", side_effect=_raise):
        proposal = _built_proposal(str(tmp_path))
        verify_proposal_bindings(proposal, str(tmp_path))
        proposal_to_authorized_semantic_regions(proposal)
    # completing without exception proves none of the patched targets were called


def test_proposal_binding_module_never_imports_write_capable_components():
    import ast
    import inspect

    import kriya.workflow.proposal_binding as pb

    tree = ast.parse(inspect.getsource(pb))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
    forbidden = {"DeveloperAgent", "AuthorizedFileWriter", "WorkflowEngine", "run_generation_workflow", "save_checkpoint"}
    assert not (imported_names & forbidden), imported_names & forbidden


# =====================================================================
# Existing A1/A2 behavior (31-34)
# =====================================================================

def test_existing_review_output_remains_valid(tmp_path):
    from kriya.workflow.review_context import format_adjudicated_review
    import inspect
    params = list(inspect.signature(format_adjudicated_review).parameters)
    assert "proposal" not in params and "workspace_root" not in params


def test_a2_proposal_still_advisory_only(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    from kriya.workflow.review_context import PROPOSAL_AUTHORITY_ADVISORY_ONLY
    assert proposal.authority == PROPOSAL_AUTHORITY_ADVISORY_ONLY


def test_a2_proposal_still_not_approved(tmp_path):
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))
    from kriya.workflow.review_context import PROPOSAL_APPROVAL_NOT_APPROVED
    assert proposal.approval == PROPOSAL_APPROVAL_NOT_APPROVED


def test_legacy_call_without_workspace_root_unchanged():
    """No tmp_path/real files at all - the exact pre-A3-P0 in-memory-only
    test shape must still work identically."""
    from kriya.analyzer.java_members import extract_java_members as _e
    src = "public class Target {\n    public void doWork() {\n    }\n}\n"
    members = _e(src)
    member_ids = build_member_evidence_ids(members)
    mid = next(iter(member_ids))
    f = _finding(mid, (mid,))
    [adj] = adjudicate_findings([f], member_ids, {})
    proposal = build_proposed_modification("F1", [adj], member_ids, {}, "Target.java")
    assert proposal.workspace_id == ""
    assert proposal.evidence_bindings == ()
    report = format_proposed_modification(proposal)
    assert "Binding:" not in report


# =====================================================================
# Integration (Part 20) - the essential end-to-end chain proof
# =====================================================================

def test_full_chain_finding_to_proposal_to_binding_to_verification_to_enforcement(tmp_path):
    """A1 finding -> A2 proposal -> durable binding -> verify (unchanged
    repo) -> translate -> AuthorizedSemanticRegion[] -> feed into CORR-018's
    pure comparator: the authorized target change is accepted, an unrelated
    same-file change is rejected. No live LLM anywhere in this chain."""
    _write_target(str(tmp_path))
    proposal = _built_proposal(str(tmp_path))

    verification = verify_proposal_bindings(proposal, str(tmp_path))
    assert verification.ok is True

    regions = proposal_to_authorized_semantic_regions(proposal)

    authorized_candidate = TARGET_SRC.replace(
        "    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n",
        "    public void delete(Long id) {\n        if (repository.existsById(id)) {\n"
        "            repository.deleteById(id);\n        }\n    }\n",
    )
    accepted = find_unauthorized_semantic_changes({RELPATH: TARGET_SRC}, {RELPATH: authorized_candidate}, regions)
    assert accepted == []

    unrelated_candidate = authorized_candidate.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        return repository.findById(id).orElse(null);\n    }\n",
    )
    rejected = find_unauthorized_semantic_changes({RELPATH: TARGET_SRC}, {RELPATH: unrelated_candidate}, regions)
    assert rejected != []

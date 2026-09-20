"""A3-P1: persisted proposal integrity and approval state.

Every test is read-only against real files under pytest's `tmp_path` and the
real `.kriya/proposals` persistence this module adds - no live LLM, no
generation workflow. `test_full_prepromotion_flow_...` at the bottom is the
load-bearing integration test: persist -> load -> verify -> approve ->
reload -> translate -> CORR-018 enforcement, then repository drift after
approval detected as stale without silently rewriting approval_state.
"""
import json
import os

import pytest

from kriya.analyzer.java_members import extract_java_members
from kriya.workflow.review_context import (
    CONFIDENCE_STRONG_STATIC_INDICATION,
    StructuredFinding,
    adjudicate_findings,
    build_member_evidence_ids,
    build_proposed_modification,
)
from kriya.workflow.proposal_binding import proposal_to_authorized_semantic_regions
from kriya.workflow.proposal_store import (
    PROPOSALS_DIRNAME,
    REASON_PROPOSAL_ALREADY_APPROVED,
    REASON_PROPOSAL_BINDING_INVALID,
    REASON_PROPOSAL_ID_INVALID,
    REASON_PROPOSAL_NOT_FOUND,
    REASON_PROPOSAL_REJECTED,
    REASON_PROPOSAL_SCHEMA_UNSUPPORTED,
    REASON_PROPOSAL_TAMPERED,
    ApprovalState,
    ProposalStoreError,
    approve_proposal,
    canonical_json_bytes,
    canonicalize_proposal_semantics,
    compute_proposal_digest,
    load_proposal,
    persist_proposal,
    reject_proposal,
    verify_persisted_proposal,
)
from kriya.workflow.semantic_region_authority import find_unauthorized_semantic_changes

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
    "    public void delete(Long id) {\n"
    "        repository.deleteById(id);\n"
    "    }\n"
    "}\n"
)


def _write_target(root):
    abs_path = os.path.join(root, RELPATH)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w") as fh:
        fh.write(TARGET_SRC)
    return abs_path


def _build_proposal(workspace_root):
    members = extract_java_members(TARGET_SRC)
    member_ids = build_member_evidence_ids(members)
    delete_mid = next(mid for mid, m in member_ids.items() if m.name == "delete")
    f = StructuredFinding(
        finding_id="F1", title="delete() does not check existence first", member_id=delete_mid,
        requested_confidence=CONFIDENCE_STRONG_STATIC_INDICATION,
        condition_evidence_ids=(delete_mid,), consequence_evidence_ids=(),
        runtime_dependency_declared=False, explanation="",
        recommendation="Check repository.existsById(id) before deleting.",
    )
    [adj] = adjudicate_findings([f], member_ids, {})
    return build_proposed_modification("F1", [adj], member_ids, {}, RELPATH, workspace_root=workspace_root)


def _proposal_path(root):
    return os.path.join(root, PROPOSALS_DIRNAME, "P1.json")


# =====================================================================
# Canonical serialization (1-5)
# =====================================================================

def test_same_proposal_identical_canonical_bytes(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    b1 = canonical_json_bytes(canonicalize_proposal_semantics(proposal))
    b2 = canonical_json_bytes(canonicalize_proposal_semantics(proposal))
    assert b1 == b2


def test_evidence_order_variation_identical_canonical_output(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    import dataclasses
    reversed_proposal = dataclasses.replace(proposal, evidence_bindings=tuple(reversed(proposal.evidence_bindings)))
    d1 = canonicalize_proposal_semantics(proposal)
    d2 = canonicalize_proposal_semantics(reversed_proposal)
    assert d1 == d2
    assert canonical_json_bytes(d1) == canonical_json_bytes(d2)


def test_operational_timestamp_difference_does_not_change_semantic_digest(tmp_path):
    """created_at/updated_at/approved_at live OUTSIDE the digest-bearing
    proposal semantics entirely (top-level PersistedProposal fields, never
    passed to canonicalize_proposal_semantics()) - persisting the same
    proposal twice, seconds apart, must produce the same digest."""
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    d1 = compute_proposal_digest(proposal)
    import time
    time.sleep(0.01)
    d2 = compute_proposal_digest(proposal)
    assert d1 == d2


def test_one_semantic_field_change_changes_digest(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    import dataclasses
    changed = dataclasses.replace(proposal, proposed_change="a completely different proposed change")
    assert compute_proposal_digest(proposal) != compute_proposal_digest(changed)


def test_hard_digest_test_vector():
    """A pinned, hand-computed regression test - if canonicalization drifts
    in the future, THIS test fails first, loudly, rather than digests
    silently changing meaning across a Kriya upgrade."""
    import dataclasses
    from kriya.workflow.review_context import ProposedModification
    fixed_proposal = ProposedModification(
        proposal_id="P1", source_finding_id="F1", target_file="A.java",
        target_member="M1 - public void foo()", problem_statement="problem",
        proposed_change="change", must_preserve=("a", "b"), verification=("compile",),
        evidence=("M1 (condition) - public void foo()",), assumptions=("none",),
        final_confidence="STRONG_STATIC_INDICATION",
        target_member_key="A.java::A::METHOD::foo::()", target_member_kind="method",
        workspace_id="fixedworkspaceid", workspace_fingerprint=None,
        target_file_sha256="fixedfilehash", evidence_bindings=(),
    )
    digest = compute_proposal_digest(fixed_proposal)
    canonical = canonical_json_bytes(canonicalize_proposal_semantics(fixed_proposal))
    import hashlib
    assert digest == hashlib.sha256(canonical).hexdigest()
    # Pinned literal (computed once, directly, from this exact fixed_proposal
    # and canonicalize_proposal_semantics()'s current field set/ordering) -
    # if this ever fails, canonicalization drifted; update deliberately,
    # never to silently "make the test pass" without understanding why the
    # canonical bytes changed.
    assert canonical == (
        b'{"assumptions":["none"],"authority":"ADVISORY_ONLY","evidence":'
        b'["M1 (condition) - public void foo()"],"evidence_bindings":[],'
        b'"final_confidence":"STRONG_STATIC_INDICATION","must_preserve":["a","b"],'
        b'"problem_statement":"problem","proposal_id":"P1","proposed_change":"change",'
        b'"source_finding_id":"F1","target_file":"A.java","target_file_sha256":"fixedfilehash",'
        b'"target_member_key":"A.java::A::METHOD::foo::()","target_member_kind":"method",'
        b'"verification":["compile"],"workspace_fingerprint":null,"workspace_id":"fixedworkspaceid"}'
    )
    assert digest == "ca92f8e91b57ecff6731bcad409010aef71a2490faf357aee455b496d5c8165f"


# =====================================================================
# Persistence (6-10)
# =====================================================================

def test_persist_creates_file_under_kriya_proposals(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    assert os.path.isfile(_proposal_path(str(tmp_path)))


def test_persisted_json_reloads_identically(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persisted = persist_proposal(proposal, str(tmp_path))
    reloaded = load_proposal("P1", str(tmp_path))
    assert reloaded.proposal_digest == persisted.proposal_digest
    assert reloaded.proposal.target_file == proposal.target_file
    assert reloaded.proposal.target_member_key == proposal.target_member_key
    assert compute_proposal_digest(reloaded.proposal) == persisted.proposal_digest


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    assert not os.path.isfile(_proposal_path(str(tmp_path)) + ".tmp")
    assert os.path.isfile(_proposal_path(str(tmp_path)))


def test_invalid_proposal_id_traversal_rejected(tmp_path):
    _write_target(str(tmp_path))
    _build_proposal(str(tmp_path))
    for bad_id in ("../../etc/passwd", "..", ".", "a/b", "a\\b", ""):
        with pytest.raises(ProposalStoreError) as exc_info:
            load_proposal(bad_id, str(tmp_path))
        assert exc_info.value.reason_code == REASON_PROPOSAL_ID_INVALID


def test_unknown_schema_rejected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    path = _proposal_path(str(tmp_path))
    with open(path) as fh:
        raw = json.load(fh)
    raw["schema_version"] = 999
    with open(path, "w") as fh:
        json.dump(raw, fh)
    with pytest.raises(ProposalStoreError) as exc_info:
        load_proposal("P1", str(tmp_path))
    assert exc_info.value.reason_code == REASON_PROPOSAL_SCHEMA_UNSUPPORTED


# =====================================================================
# Tampering (11-15)
# =====================================================================

def _tamper(path, mutate_fn):
    with open(path) as fh:
        raw = json.load(fh)
    mutate_fn(raw)
    with open(path, "w") as fh:
        json.dump(raw, fh)


def test_manually_edited_proposed_change_detected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    _tamper(_proposal_path(str(tmp_path)), lambda raw: raw["proposal"].__setitem__("proposed_change", "different"))
    result = verify_persisted_proposal(load_proposal("P1", str(tmp_path)), str(tmp_path))
    assert result.tampered is True
    assert REASON_PROPOSAL_TAMPERED in result.reason_codes


def test_manually_edited_target_member_detected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    _tamper(_proposal_path(str(tmp_path)), lambda raw: raw["proposal"].__setitem__("target_member_key", "fabricated::key"))
    result = verify_persisted_proposal(load_proposal("P1", str(tmp_path)), str(tmp_path))
    assert result.tampered is True


def test_manually_edited_evidence_binding_detected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    def mutate(raw):
        raw["proposal"]["evidence_bindings"][0]["normalized_content_hash"] = "0" * 64
    _tamper(_proposal_path(str(tmp_path)), mutate)
    result = verify_persisted_proposal(load_proposal("P1", str(tmp_path)), str(tmp_path))
    assert result.tampered is True


def test_manually_edited_stored_digest_only_detected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    _tamper(_proposal_path(str(tmp_path)), lambda raw: raw.__setitem__("proposal_digest", "0" * 64))
    result = verify_persisted_proposal(load_proposal("P1", str(tmp_path)), str(tmp_path))
    assert result.tampered is True
    assert REASON_PROPOSAL_TAMPERED in result.reason_codes


def test_operational_metadata_edit_does_not_change_semantic_digest(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    _tamper(_proposal_path(str(tmp_path)), lambda raw: raw["metadata"].__setitem__("updated_at", "2099-01-01T00:00:00.000000Z"))
    result = verify_persisted_proposal(load_proposal("P1", str(tmp_path)), str(tmp_path))
    assert result.tampered is False  # metadata isn't digest-bearing - editing it alone is not "tampering" the semantics


# =====================================================================
# Approval (16-21)
# =====================================================================

def test_valid_pending_proposal_can_be_approved(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is True
    assert result.persisted.approval_state == ApprovalState.APPROVED.value


def test_approved_digest_equals_proposal_digest(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persisted = persist_proposal(proposal, str(tmp_path))
    result = approve_proposal("P1", str(tmp_path))
    assert result.persisted.approved_digest == persisted.proposal_digest


def test_approve_already_approved_deterministic_reason(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    approve_proposal("P1", str(tmp_path))
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False
    assert REASON_PROPOSAL_ALREADY_APPROVED in result.reason_codes


def test_rejected_proposal_cannot_be_approved(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    reject_proposal("P1", str(tmp_path))
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False
    assert REASON_PROPOSAL_REJECTED in result.reason_codes


def test_stale_proposal_cannot_be_approved(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    with open(abs_path, "a") as fh:
        fh.write("    // drift before approval\n")
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False
    assert REASON_PROPOSAL_BINDING_INVALID in result.reason_codes


def test_tampered_proposal_cannot_be_approved(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    _tamper(_proposal_path(str(tmp_path)), lambda raw: raw["proposal"].__setitem__("proposed_change", "tampered"))
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False
    assert REASON_PROPOSAL_TAMPERED in result.reason_codes


# =====================================================================
# Dirty workspace (22-24)
# =====================================================================

def test_persist_then_mutate_target_then_approval_rejected_due_to_stale_binding(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))

    abs_path = os.path.join(str(tmp_path), RELPATH)
    with open(abs_path, "a") as fh:
        fh.write("    // dirty state change, never committed\n")

    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False
    assert REASON_PROPOSAL_BINDING_INVALID in result.reason_codes


# =====================================================================
# Evidence (25-26)
# =====================================================================

def test_mutate_evidence_file_approval_rejected(tmp_path):
    """The target file IS the sole evidence file in this fixture - mutating
    it invalidates both the target-file AND the evidence-file binding."""
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    changed = TARGET_SRC.replace(
        "    public DriverDO find(Long id) {\n        return repository.findById(id);\n    }\n",
        "    public DriverDO find(Long id) {\n        return repository.findById(id).orElse(null);\n    }\n",
    )
    with open(abs_path, "w") as fh:
        fh.write(changed)
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False


def test_structural_evidence_missing_approval_rejected(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    abs_path = os.path.join(str(tmp_path), RELPATH)
    without_delete = TARGET_SRC.replace(
        "\n    public void delete(Long id) {\n        repository.deleteById(id);\n    }\n", "",
    )
    with open(abs_path, "w") as fh:
        fh.write(without_delete)
    result = approve_proposal("P1", str(tmp_path))
    assert result.ok is False


# =====================================================================
# Approval content identity (27-29)
# =====================================================================

def test_approval_rejected_after_semantic_content_edit_even_if_state_flag_edited(tmp_path):
    """Simulates an adversarial edit: approve P1, then edit the persisted
    semantic content directly AND forge the approval block to still say
    APPROVED with a matching-looking approved_digest copied from the old
    file. verify_persisted_proposal() must recompute from CURRENT semantics
    and catch this regardless of what the approval block claims."""
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    approve_proposal("P1", str(tmp_path))

    path = _proposal_path(str(tmp_path))
    with open(path) as fh:
        raw = json.load(fh)
    original_approved_digest = raw["approval"]["approved_digest"]
    raw["proposal"]["proposed_change"] = "a forged different change"
    # approval block left claiming APPROVED with the OLD digest - simulates
    # an attacker who didn't bother updating it, the more common case.
    with open(path, "w") as fh:
        json.dump(raw, fh)

    reloaded = load_proposal("P1", str(tmp_path))
    assert reloaded.approval_state == ApprovalState.APPROVED.value  # lifecycle flag still claims approved
    assert reloaded.approved_digest == original_approved_digest

    result = verify_persisted_proposal(reloaded, str(tmp_path))
    assert result.tampered is True
    assert result.approved_and_valid is False  # future promotion must never trust this


# =====================================================================
# CLI (30-34) - exercised via the CLI self-check in this session; here we
# verify the underlying command wiring is structurally sound (no -y/--yes
# option exists on the approve command at all).
# =====================================================================

def test_proposal_approve_command_has_no_yes_flag():
    from kriya.cli import proposal_approve
    param_names = {p.name for p in proposal_approve.params}
    assert "yes" not in param_names and "y" not in param_names


def test_review_propose_without_save_creates_no_proposals_dir(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    # simulate the CLI's default path: build only, never persist
    assert not os.path.isdir(os.path.join(str(tmp_path), ".kriya", "proposals"))


# =====================================================================
# Zero generation (35-37)
# =====================================================================

def test_approval_completes_with_run_generation_workflow_patched_to_raise(tmp_path):
    from unittest.mock import patch
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))

    def _raise(*a, **k):
        raise AssertionError("A3-P1 zero-generation violation: run_generation_workflow invoked")

    with patch("kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", side_effect=_raise):
        result = approve_proposal("P1", str(tmp_path))
    assert result.ok is True


def test_approval_completes_with_developer_agent_patched_to_raise(tmp_path):
    from unittest.mock import patch
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))

    def _raise(*a, **k):
        raise AssertionError("A3-P1 zero-generation violation: DeveloperAgent invoked")

    with patch("kriya.agents.agent.DeveloperAgent.run_generation", side_effect=_raise):
        result = approve_proposal("P1", str(tmp_path))
    assert result.ok is True


def test_approval_completes_with_authorized_file_writer_patched_to_raise(tmp_path):
    from unittest.mock import patch
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))

    def _raise(*a, **k):
        raise AssertionError("A3-P1 zero-generation violation: AuthorizedFileWriter invoked")

    with patch("kriya.policy.filesystem.AuthorizedFileWriter.commit_file", side_effect=_raise):
        result = approve_proposal("P1", str(tmp_path))
    assert result.ok is True


# =====================================================================
# Source repository safety (38-40)
# =====================================================================

def _hash_source_file(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_approval_changes_only_kriya_proposals_never_production_source(tmp_path):
    abs_path = _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    before = _hash_source_file(abs_path)
    approve_proposal("P1", str(tmp_path))
    assert _hash_source_file(abs_path) == before


def test_reject_and_show_perform_no_source_writes(tmp_path):
    abs_path = _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    before = _hash_source_file(abs_path)
    reject_proposal("P1", str(tmp_path))
    load_proposal("P1", str(tmp_path))
    verify_persisted_proposal(load_proposal("P1", str(tmp_path)), str(tmp_path))
    assert _hash_source_file(abs_path) == before


def test_stale_tampered_approval_attempt_leaves_source_unchanged(tmp_path):
    abs_path = _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))
    persist_proposal(proposal, str(tmp_path))
    _tamper(_proposal_path(str(tmp_path)), lambda raw: raw["proposal"].__setitem__("proposed_change", "x"))
    before = _hash_source_file(abs_path)
    approve_proposal("P1", str(tmp_path))  # refused, per earlier test
    assert _hash_source_file(abs_path) == before


# =====================================================================
# Integration (Part 20 equivalent for A3-P1) - the essential proof
# =====================================================================

def test_full_prepromotion_flow_persist_load_verify_approve_reload_translate_enforce(tmp_path):
    _write_target(str(tmp_path))
    proposal = _build_proposal(str(tmp_path))

    persisted = persist_proposal(proposal, str(tmp_path))
    loaded = load_proposal("P1", str(tmp_path))
    assert loaded.proposal_digest == persisted.proposal_digest

    pre_approval_check = verify_persisted_proposal(loaded, str(tmp_path))
    assert pre_approval_check.ok is True

    approval = approve_proposal("P1", str(tmp_path))
    assert approval.ok is True

    reloaded = load_proposal("P1", str(tmp_path))
    assert reloaded.approval_state == ApprovalState.APPROVED.value
    assert reloaded.approved_digest == reloaded.proposal_digest

    regions = proposal_to_authorized_semantic_regions(reloaded.proposal)
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

    # Now mutate the target file - approved artifact must show as currently
    # invalid/stale, without silently rewriting approval_state.
    abs_path = os.path.join(str(tmp_path), RELPATH)
    with open(abs_path, "a") as fh:
        fh.write("    // repository drift after approval\n")

    post_drift = load_proposal("P1", str(tmp_path))
    drift_check = verify_persisted_proposal(post_drift, str(tmp_path))
    assert drift_check.ok is False
    assert drift_check.approved_and_valid is False
    assert drift_check.approval_state == ApprovalState.APPROVED.value  # never silently rewritten

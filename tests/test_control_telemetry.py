"""MA5.10: control-plane telemetry (kriya/control/telemetry.py) - each
record function produces exactly the small, safe fields section 31 calls
for, and never leaks large proprietary content (a contract's full shape,
a context item's full file content)."""

from kriya.control.artifacts import ArtifactRecord, ArtifactValidationResult
from kriya.control.contracts import ContractChange, ContractRecord, ContractState
from kriya.control.decisions import DecisionLedger
from kriya.control.telemetry import (
    record_artifact_derivation,
    record_artifact_drift,
    record_contract_access,
    record_contract_change,
    record_context_package_summary,
    record_omitted_context,
    record_registry_hashes,
    record_resume_validation,
)
from kriya.workflow.checkpoint import ResumeStatus, ResumeValidationResult
from kriya.workflow.context_package import build_context_package, make_context_item, make_omitted_entry


def test_record_contract_access_never_includes_the_shape():
    record = ContractRecord(
        id="M1:X", name="X", provider_milestone_id="M1",
        shape={"secret_proprietary_field": "should never leak"}, state=ContractState.PROPOSED,
    )
    ledger = DecisionLedger()
    decision = record_contract_access(ledger, record, access="read")
    assert decision.type == "contract_access"
    assert decision.fields["access"] == "read"
    assert decision.fields["contract_id"] == "M1:X"
    assert "shape" not in decision.fields
    assert "secret_proprietary_field" not in str(decision.fields)


def test_record_contract_change_covers_invalidation_and_new_revision():
    change = ContractChange(
        contract_id="M1:X", old_revision="v1", proposed_shape={"a": 1}, reason="rework",
        affected_consumers=("M2", "M3"),
    )
    new_record = ContractRecord(id="M1:X", name="X", provider_milestone_id="M1", shape={"a": 1}, state=ContractState.PROPOSED, revision="v2")
    ledger = DecisionLedger()
    decision = record_contract_change(ledger, change, new_record)
    assert decision.fields["affected_consumers"] == ["M2", "M3"]
    assert decision.fields["old_revision"] == "v1"
    assert decision.fields["new_revision"] == "v2"


def test_record_contract_change_without_new_record_omits_new_revision_fields():
    change = ContractChange(contract_id="M1:X", old_revision="v1", proposed_shape={}, reason="x")
    ledger = DecisionLedger()
    decision = record_contract_change(ledger, change, None)
    assert "new_revision" not in decision.fields


def test_record_artifact_derivation_carries_only_small_coordinate_fields():
    record = ArtifactRecord(
        milestone_id="M1", ecosystem="maven", kind="library", module_path="",
        coordinates={"groupId": "com.example", "artifactId": "app", "version": "1.0"},
    )
    ledger = DecisionLedger()
    decision = record_artifact_derivation(ledger, record)
    assert decision.fields["ecosystem"] == "maven"
    assert decision.fields["coordinates"]["artifactId"] == "app"


def test_record_artifact_drift_includes_both_recorded_and_current_coordinates():
    recorded = ArtifactRecord(milestone_id="M1", ecosystem="maven", kind="library", coordinates={"artifactId": "app", "version": "1.0"})
    current = ArtifactRecord(milestone_id="M1", ecosystem="maven", kind="library", coordinates={"artifactId": "app", "version": "2.0"})
    result = ArtifactValidationResult(recorded=recorded, current=current, drifted=True, reason_code="ARTIFACT_DRIFT")
    ledger = DecisionLedger()
    decision = record_artifact_drift(ledger, result)
    assert decision.fields["drifted"] is True
    assert decision.fields["recorded_coordinates"]["version"] == "1.0"
    assert decision.fields["current_coordinates"]["version"] == "2.0"


def test_record_artifact_drift_handles_a_disappeared_artifact():
    recorded = ArtifactRecord(milestone_id="M1", ecosystem="maven", kind="library", coordinates={"artifactId": "app"})
    result = ArtifactValidationResult(recorded=recorded, current=None, drifted=True, reason_code="ARTIFACT_DRIFT")
    ledger = DecisionLedger()
    decision = record_artifact_drift(ledger, result)
    assert decision.fields["current_coordinates"] is None


def test_record_context_package_summary_never_includes_file_content():
    item = make_context_item(path="secret.py", content="proprietary source code here", reason="x", source_type="semantic_hit", trust_level="repository")
    package = build_context_package(relevant_files=(item,), token_count=10)
    ledger = DecisionLedger()
    decision = record_context_package_summary(ledger, package)
    assert decision.fields["relevant_file_count"] == 1
    assert decision.fields["provenance_counts"] == {"semantic_hit": 1}
    assert "proprietary source code here" not in str(decision.fields)


def test_record_omitted_context_captures_the_full_omitted_list():
    omitted = (make_omitted_entry("big.py", 1, "over budget", 5000),)
    package = build_context_package(omitted=omitted)
    ledger = DecisionLedger()
    decision = record_omitted_context(ledger, package)
    assert decision.fields["omitted_count"] == 1
    assert decision.fields["omitted"][0]["path"] == "big.py"


def test_record_registry_hashes():
    ledger = DecisionLedger()
    decision = record_registry_hashes(ledger, control_state_hash="csh", contract_hash="ch", artifact_registry_hash="ah")
    assert decision.fields == {"control_state_hash": "csh", "contract_hash": "ch", "artifact_registry_hash": "ah"}


def test_record_resume_validation_ok():
    ledger = DecisionLedger()
    result = ResumeValidationResult(status=ResumeStatus.OK)
    decision = record_resume_validation(ledger, result)
    assert decision.fields["status"] == "ok"
    assert decision.fields["mismatch_count"] == 0


def test_record_resume_validation_needs_review_includes_mismatches():
    ledger = DecisionLedger()
    result = ResumeValidationResult(status=ResumeStatus.NEEDS_REVIEW, mismatches=("base_commit: x", "tree_hash: y"))
    decision = record_resume_validation(ledger, result)
    assert decision.fields["status"] == "needs_review"
    assert decision.fields["mismatch_count"] == 2
    assert decision.fields["mismatches"] == ["base_commit: x", "tree_hash: y"]


def test_recorded_decisions_actually_land_in_the_ledger():
    ledger = DecisionLedger()
    record_registry_hashes(ledger, control_state_hash="x")
    assert len(ledger.all()) == 1
    assert ledger.all()[0].type == "registry_hashes"

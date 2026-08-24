"""Control-plane telemetry - MA5.10 of the control-plane implementation
plan. Thin, pure record-builder functions over kriya/control/decisions.py's
DecisionLedger (MA5.5) - each one produces exactly one Decision for one of
section 31's nine listed categories (contract reads/writes, contract
invalidations, artifact derivations, artifact drift, context package size,
context provenance, omitted context, registry hashes, resume validation
outcomes).

"Do not log full proprietary content unnecessarily" (section 31) is
enforced by what these functions DON'T take: a ContractRecord's `shape`,
a ContextItem's `content`, and an omitted entry's nothing-but-path/rank/
reason/estimated_tokens are never passed through here - only ids, states,
hashes, counts, and small structural summaries (DecisionLedger.record()
itself also truncates any long string value defensively, but these
functions are designed to never need that safety net in the first place).

Each function returns the Decision it recorded (in-memory only, via
DecisionLedger.record()) - persisting it to disk is the caller's own
choice (DecisionLedger.append_to_file(), or record_and_persist() for
both in one call), same as any other DecisionLedger usage.
"""

from collections import Counter
from typing import Any, Dict, Optional

from kriya.control.artifacts import ArtifactRecord, ArtifactValidationResult
from kriya.control.contracts import ContractChange, ContractRecord
from kriya.control.decisions import Decision, DecisionLedger
from kriya.workflow.checkpoint import ResumeValidationResult
from kriya.workflow.context_package import ContextPackage


def record_contract_access(ledger: DecisionLedger, record: ContractRecord, access: str) -> Decision:
    """`access` is "read" or "write" (section 31's "contract reads/writes") -
    a caller reports which one happened; this function doesn't guess from
    the record alone (get() vs. a lifecycle transition look identical from
    a ContractRecord's own fields)."""

    return ledger.record(
        "contract_access", access=access, contract_id=record.id, name=record.name,
        provider_milestone_id=record.provider_milestone_id, state=record.state.value,
        revision=record.revision, content_hash=record.content_hash,
    )


def record_contract_change(ledger: DecisionLedger, change: ContractChange, new_record: Optional[ContractRecord]) -> Decision:
    """Covers both "contract invalidations" (affected_consumers is exactly
    the invalidation list, per kriya/control/contracts.py's own
    propose_change docstring) and, when new_record is given (apply_change
    already succeeded), the resulting new revision/hash - one record either
    way, not two."""

    fields: Dict[str, Any] = {
        "contract_id": change.contract_id,
        "old_revision": change.old_revision,
        "reason": change.reason,
        "affected_consumers": list(change.affected_consumers),
    }
    if new_record is not None:
        fields["new_revision"] = new_record.revision
        fields["new_content_hash"] = new_record.content_hash
    return ledger.record("contract_change", **fields)


def record_artifact_derivation(ledger: DecisionLedger, record: ArtifactRecord) -> Decision:
    return ledger.record(
        "artifact_derivation", milestone_id=record.milestone_id, ecosystem=record.ecosystem,
        kind=record.kind, module_path=record.module_path, coordinates=dict(record.coordinates),
        packaging=record.packaging,
    )


def record_artifact_drift(ledger: DecisionLedger, result: ArtifactValidationResult) -> Decision:
    return ledger.record(
        "artifact_drift", drifted=result.drifted, reason_code=result.reason_code,
        milestone_id=result.recorded.milestone_id, ecosystem=result.recorded.ecosystem,
        module_path=result.recorded.module_path,
        recorded_coordinates=dict(result.recorded.coordinates),
        current_coordinates=dict(result.current.coordinates) if result.current else None,
    )


def _context_package_summary_fields(package: ContextPackage) -> Dict[str, Any]:
    """"context package size" + "context provenance" together - a
    per-source_type count breakdown, never any item's real content.
    Pure (no ledger side effect) so a caller that wants these fields under
    a DIFFERENT decision type/with extra tags (e.g. MA6.12's
    kriya/workflow/subtask_telemetry.py::record_context_package_for_subtask,
    which adds plan_id/subtask_id) can reuse the exact computation without
    also recording this module's own "context_package_built" event a
    second time as a side effect."""

    provenance_counts = Counter(item.source_type for item in package.relevant_files)
    return {
        "package_hash": package.package_hash, "token_count": package.token_count,
        "relevant_file_count": len(package.relevant_files), "contract_entry_count": len(package.contract_entries),
        "artifact_entry_count": len(package.artifact_entries), "provenance_counts": dict(provenance_counts),
    }


def record_context_package_summary(ledger: DecisionLedger, package: ContextPackage) -> Decision:
    return ledger.record("context_package_built", **_context_package_summary_fields(package))


def record_omitted_context(ledger: DecisionLedger, package: ContextPackage) -> Decision:
    """One record per build listing what was left out and why (section
    27's own path/rank/reason/estimated_tokens shape, unchanged) - never
    silently absent from telemetry just because it was already silently
    absent from the package itself."""

    return ledger.record(
        "context_omitted", package_hash=package.package_hash, omitted_count=len(package.omitted),
        omitted=list(package.omitted),
    )


def record_registry_hashes(
    ledger: DecisionLedger,
    control_state_hash: Optional[str] = None,
    contract_hash: Optional[str] = None,
    artifact_registry_hash: Optional[str] = None,
) -> Decision:
    return ledger.record(
        "registry_hashes", control_state_hash=control_state_hash,
        contract_hash=contract_hash, artifact_registry_hash=artifact_registry_hash,
    )


def record_resume_validation(ledger: DecisionLedger, result: ResumeValidationResult) -> Decision:
    return ledger.record(
        "resume_validation", status=result.status.value, mismatch_count=len(result.mismatches),
        mismatches=list(result.mismatches),
    )

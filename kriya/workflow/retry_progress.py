"""PRD-026: the canonical retry progress vector and its invariant.

Every failed attempt is reduced to a ``ProgressVector``, the stable, material
state a retry could change:
- the authoritative failure signature;
- the candidate/workspace hash;
- the implicated and missing files;
- the retry-evidence fingerprint;
- the context revision set;
- the retry action and protocol phase;
- the Developer request-profile identity;
- the plan revision;
- the repair-contract revision;
- the deterministic diagnostics.
Timestamps, attempt numbers, raw model text and prompt wording are never
part of the vector, so variation in them cannot look like progress.

The invariant, enforced by ``classify_progress`` (called from
``retry_strategy.record_workspace_progress``):

* A vector already produced earlier in the run is NOT progress. That holds
  for an immediate repeat and for an alternating cycle (A -> B -> A -> B).
  The run keeps every digest it has seen, and a repeat is counted
  toward the configured no-progress bound, never reset.
* SAMPLING_RESAMPLE is a separate typed allowance, not progress. The same
  retry evidence may be sent to the Developer again only when:
  - the effective sampling temperature is above zero;
  - the retry family permits stochastic resampling;
  - the normal retry budget remains (the existing budgets decide that).
  A resample consumes budget, never resets the no-progress counter and never
  erases seen vectors. Only a genuinely new resulting vector counts as
  progress.

``RETRY_ACTION_MATERIAL_DELTA`` is the audit table: for each RetryAction,
the vector dimensions whose change makes another attempt of that family
useful.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Iterable, Optional, Tuple

from kriya.workflow.retry_policy import RetryAction

PROGRESS_VECTOR_VERSION = "progress-vector/1"

# Classifications. PROGRESS/NO_PROGRESS/REPEATED_ACTION/REGRESSION predate
# PRD-026 (record_workspace_progress); REPEATED_VECTOR is the new one.
PROGRESS = "PROGRESS"
NO_PROGRESS = "NO_PROGRESS"
REPEATED_ACTION = "REPEATED_ACTION"
REGRESSION = "REGRESSION"
REPEATED_VECTOR = "REPEATED_VECTOR"

SAMPLING_RESAMPLE = "SAMPLING_RESAMPLE"
# Terminal reason when the configured no-progress bound is reached.
NO_PROGRESS_TERMINAL_REASON = "RETRY_NO_PROGRESS_EXHAUSTED"
# LR-R1-P4: a failed verification-only attempt whose next attempt could only
# repeat the same verification on the same inputs (no mutation authority, no
# changed input, no recovery route): stopped before an equivalent retry.
VERIFICATION_RETRY_NO_CHANGE_POSSIBLE = "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"
# Reason an identical-evidence retry is refused (no stochastic variation).
SAMPLING_NOT_PERMITTED = "SAMPLING_NOT_PERMITTED"

# Retry families whose output may legitimately vary on an identical input
# (a probabilistic Developer generation). API-contract recovery is a
# deterministic state machine and never resamples.
SAMPLING_ELIGIBLE_MODES = frozenset({
    RetryAction.TARGETED.value, RetryAction.MISSING_FILES.value,
    RetryAction.FALLBACK_TARGETED.value, RetryAction.FULL_SET.value,
})

# Audit table (PRD-026 requirement 2): the vector dimensions whose change
# makes another attempt of each family useful. SAMPLING_RESAMPLE is the only
# other justification, and only for SAMPLING_ELIGIBLE_MODES.
RETRY_ACTION_MATERIAL_DELTA: Dict[RetryAction, Tuple[str, ...]] = {
    RetryAction.TARGETED: (
        "workspace_hash", "failure_signature", "implicated_files", "evidence_fingerprint", "context_revisions",
    ),
    RetryAction.MISSING_FILES: ("missing_files", "workspace_hash"),
    RetryAction.FALLBACK_TARGETED: ("request_profile",),
    RetryAction.FULL_SET: ("action", "workspace_hash", "failure_signature", "request_profile"),
    RetryAction.API_CONTRACT_RECOVERY: ("protocol", "workspace_hash", "repair_contract_revision"),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=repr, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()


@dataclass(frozen=True)
class ProgressVector:
    failure_signature: str
    workspace_hash: str
    implicated_files: Tuple[str, ...]
    missing_files: Tuple[str, ...]
    evidence_fingerprint: str
    context_revisions: Tuple[Tuple[str, ...], ...]
    action: str
    protocol: str
    request_profile: str
    plan_revision: str
    repair_contract_revision: str
    diagnostics: Tuple[str, ...]

    def digest(self) -> str:
        return _hash([PROGRESS_VECTOR_VERSION, asdict(self)])

    def changed_dimensions(self, previous: Optional["ProgressVector"]) -> Tuple[str, ...]:
        if previous is None:
            return tuple(field.name for field in fields(self))
        return tuple(
            field.name for field in fields(self)
            if getattr(self, field.name) != getattr(previous, field.name)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"version": PROGRESS_VECTOR_VERSION, "digest": self.digest(), **asdict(self)}


def build_progress_vector(
    *,
    failure_signature: Any,
    workspace_hash: str,
    implicated_files: Iterable[str] = (),
    missing_files: Iterable[str] = (),
    evidence_fingerprint: Any = None,
    context_items: Optional[Dict[str, Any]] = None,
    action: Optional[str] = None,
    protocol: Optional[str] = None,
    request_profile: Optional[str] = None,
    plan_revision: Optional[str] = None,
    repair_contract: Any = None,
    diagnostics: Iterable[str] = (),
) -> ProgressVector:
    """Normalize the raw per-attempt state into a canonical vector. Every
    input is reduced to a stable string or a sorted tuple."""
    revisions = tuple(sorted(
        (
            path,
            str(getattr(item, "revision", "") or ""),
            str(getattr(item, "tier", "") or ""),
            str(getattr(item, "member_id", "") or ""),
        )
        for path, item in (context_items or {}).items()
    ))
    contract_revision = ""
    if repair_contract is not None:
        contract_revision = _hash([
            getattr(repair_contract, "id", None),
            getattr(getattr(repair_contract, "status", None), "value", None),
            getattr(repair_contract, "active_group_id", None),
            list(getattr(repair_contract, "participating_artifacts", ()) or ()),
        ])
    return ProgressVector(
        failure_signature=_hash(failure_signature) if failure_signature is not None else "",
        workspace_hash=workspace_hash or "",
        implicated_files=tuple(sorted(set(implicated_files or ()))),
        missing_files=tuple(sorted(set(missing_files or ()))),
        evidence_fingerprint=_hash(evidence_fingerprint) if evidence_fingerprint is not None else "",
        context_revisions=revisions,
        action=action or "",
        protocol=protocol or "",
        request_profile=request_profile or "",
        plan_revision=plan_revision or "",
        repair_contract_revision=contract_revision,
        diagnostics=tuple(sorted({str(code) for code in diagnostics or () if code})),
    )


def classify_progress(
    *,
    already_seen: bool,
    same_workspace: bool,
    action_changed: bool,
    stage_regressed: bool,
    repeated_action: bool,
) -> Tuple[str, bool]:
    """Return (classification, counts_as_no_progress). A seen vector is
    never progress. Otherwise the pre-PRD-026 rules apply unchanged: a new
    workspace is progress; a new action on the same workspace is a strategy
    transition (a new vector, so the counter restarts); anything else on
    the same workspace counts."""
    if already_seen:
        return REPEATED_VECTOR, True
    if not same_workspace:
        return PROGRESS, False
    if action_changed:
        return NO_PROGRESS, False
    if stage_regressed:
        return REGRESSION, True
    if repeated_action:
        return REPEATED_ACTION, True
    return NO_PROGRESS, True


def sampling_resample_permitted(mode: Optional[str], effective_temperature: Optional[float]) -> bool:
    """Whether an identical-evidence retry may be spent on stochastic
    variation: only for a sampling-eligible family, and only when the model
    samples (temperature above zero). A None temperature is the provider
    default, which Kriya cannot prove is deterministic, so it counts as
    sampling."""
    if mode not in SAMPLING_ELIGIBLE_MODES:
        return False
    return effective_temperature is None or effective_temperature > 0

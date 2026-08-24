"""ContextPackage - MA5.6 of the control-plane implementation plan.

A structured, hashable snapshot of everything a role (Planner/Architect/
Developer) was actually shown for one generation attempt - conventions,
the relevant-file slice with real provenance, a spec slice, contract/
artifact summaries, and what was deliberately left OUT and why. Built by
kriya/workflow/context_orchestrator.py (MA5.7); this module only defines
the shape.

Immutable once handed to a role (section 22): both ContextItem and
ContextPackage are frozen dataclasses, and package_hash is computed once
at construction from the package's own real content - a caller that needs
a DIFFERENT context creates a new ContextPackage (with_changes() below),
never mutates an already-built one a plan/prompt was already generated
from. This is the same "frozen + with_* returns a new instance" contract
every MA1-5 domain object already follows (kriya/workflow/triage.py's
EngineeringRoute, kriya/control/state.py's ControlState, ...).

contract_entries/artifact_entries/omitted are deliberately kept as plain
dicts (Tuple[Dict[str, Any], ...]), not typed dataclasses wrapping
ContractRecord/ArtifactRecord - a ContextPackage is a SNAPSHOT/SUMMARY
projection (see contract_entry_from_record/artifact_entry_from_record
below), not the live registry objects themselves; a role consuming a
ContextPackage should never be able to reach back into a live
ContractRegistry/ArtifactRegistry through it.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple, Union

from kriya.control.artifacts import ArtifactRecord
from kriya.control.contracts import ContractRecord
from kriya.policy.trust import TrustLevel


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _trust_level_str(trust_level: Union[str, TrustLevel]) -> str:
    # TrustLevel is an IntEnum (kriya/policy/trust.py) - .value is an int
    # (0-5, its ordering), not a readable string; .name.lower() is the
    # actual human-readable form ("external", "repository", ...) this
    # snapshot wants.
    return trust_level.name.lower() if isinstance(trust_level, TrustLevel) else str(trust_level)


@dataclass(frozen=True)
class ContextItem:
    """One piece of retrieved/carried-forward context, with real
    provenance (why it's here - section 26's own vocabulary, see
    CONTEXT_SOURCE_TYPES below) and trust metadata (kriya/policy/trust.py's
    TrustLevel, stored by its string value so this stays a plain,
    JSON-serializable snapshot)."""

    path: str
    content: str
    reason: str
    source_type: str
    trust_level: str

    score: Optional[float] = None
    content_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "content": self.content,
            "reason": self.reason,
            "score": self.score,
            "source_type": self.source_type,
            "trust_level": self.trust_level,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextItem":
        return cls(
            path=data["path"], content=data["content"], reason=data["reason"],
            source_type=data["source_type"], trust_level=data["trust_level"],
            score=data.get("score"), content_hash=data.get("content_hash", ""),
        )


# Section 26's own provenance vocabulary, verbatim - every ContextItem's
# source_type SHOULD be one of these, though the field itself stays a
# plain str (not this enum) so a future provenance kind doesn't require a
# dataclass/enum change to even construct a ContextItem with it.
CONTEXT_SOURCE_TYPES: Tuple[str, ...] = (
    "named_in_request",
    "stack_trace_frame",
    "lsp_reference",
    "graph_dependency",
    "semantic_hit",
    "contract_provider",
    "artifact_dependency",
    "carried_forward_acceptance",
)


def make_context_item(
    path: str, content: str, reason: str, source_type: str,
    trust_level: Union[str, TrustLevel], score: Optional[float] = None,
) -> ContextItem:
    """The one real constructor path - computes content_hash automatically
    so a caller can never hand-construct a ContextItem with a hash that
    doesn't actually match its content."""

    return ContextItem(
        path=path, content=content, reason=reason, source_type=source_type,
        trust_level=_trust_level_str(trust_level), score=score, content_hash=_content_hash(content),
    )


def make_omitted_entry(path: str, rank: int, reason: str, estimated_tokens: int) -> Dict[str, Any]:
    """section 27: every omitted entry carries path/rank/reason/
    estimated_tokens - never a silent truncation with no record of what
    was cut or why."""

    return {"path": path, "rank": rank, "reason": reason, "estimated_tokens": estimated_tokens}


def contract_entry_from_record(record: ContractRecord) -> Dict[str, Any]:
    return record.to_dict()


def artifact_entry_from_record(record: ArtifactRecord) -> Dict[str, Any]:
    return record.to_dict()


def _package_hash(fields_without_hash: Dict[str, Any]) -> str:
    blob = json.dumps(fields_without_hash, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContextPackage:
    conventions: Dict[str, Any] = field(default_factory=dict)

    relevant_files: Tuple[ContextItem, ...] = ()

    spec_slice: Optional[Dict[str, Any]] = None

    carried_forward_criteria: Tuple[str, ...] = ()

    contract_entries: Tuple[Dict[str, Any], ...] = ()
    artifact_entries: Tuple[Dict[str, Any], ...] = ()

    baseline: Optional[Dict[str, Any]] = None

    omitted: Tuple[Dict[str, Any], ...] = ()

    token_count: int = 0
    package_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conventions": self.conventions,
            "relevant_files": [item.to_dict() for item in self.relevant_files],
            "spec_slice": self.spec_slice,
            "carried_forward_criteria": list(self.carried_forward_criteria),
            "contract_entries": list(self.contract_entries),
            "artifact_entries": list(self.artifact_entries),
            "baseline": self.baseline,
            "omitted": list(self.omitted),
            "token_count": self.token_count,
            "package_hash": self.package_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextPackage":
        return cls(
            conventions=dict(data.get("conventions", {})),
            relevant_files=tuple(ContextItem.from_dict(i) for i in data.get("relevant_files", ())),
            spec_slice=data.get("spec_slice"),
            carried_forward_criteria=tuple(data.get("carried_forward_criteria", ())),
            contract_entries=tuple(data.get("contract_entries", ())),
            artifact_entries=tuple(data.get("artifact_entries", ())),
            baseline=data.get("baseline"),
            omitted=tuple(data.get("omitted", ())),
            token_count=data.get("token_count", 0),
            package_hash=data.get("package_hash", ""),
        )

    def with_changes(self, **changes: Any) -> "ContextPackage":
        """Returns a NEW ContextPackage with package_hash recomputed from
        the resulting content - never mutates self. A caller passing
        package_hash directly is a mistake (it would be silently
        overwritten by the recomputed value anyway), so this pops it if
        present rather than letting it look like it had any effect."""

        changes = dict(changes)
        changes.pop("package_hash", None)
        candidate = replace(self, **changes)
        return build_context_package(**{k: getattr(candidate, k) for k in _CONTENT_FIELDS})


_CONTENT_FIELDS: Tuple[str, ...] = (
    "conventions", "relevant_files", "spec_slice", "carried_forward_criteria",
    "contract_entries", "artifact_entries", "baseline", "omitted", "token_count",
)


def build_context_package(
    conventions: Optional[Dict[str, Any]] = None,
    relevant_files: Tuple[ContextItem, ...] = (),
    spec_slice: Optional[Dict[str, Any]] = None,
    carried_forward_criteria: Tuple[str, ...] = (),
    contract_entries: Tuple[Dict[str, Any], ...] = (),
    artifact_entries: Tuple[Dict[str, Any], ...] = (),
    baseline: Optional[Dict[str, Any]] = None,
    omitted: Tuple[Dict[str, Any], ...] = (),
    token_count: int = 0,
) -> ContextPackage:
    """The one real constructor path for a ContextPackage - computes
    package_hash automatically from the real content, the same "never
    hand-construct with a hash that doesn't match" discipline
    make_context_item() already applies to ContextItem."""

    content = {
        "conventions": conventions or {},
        "relevant_files": [item.to_dict() for item in relevant_files],
        "spec_slice": spec_slice,
        "carried_forward_criteria": list(carried_forward_criteria),
        "contract_entries": list(contract_entries),
        "artifact_entries": list(artifact_entries),
        "baseline": baseline,
        "omitted": list(omitted),
        "token_count": token_count,
    }
    return ContextPackage(
        conventions=conventions or {},
        relevant_files=relevant_files,
        spec_slice=spec_slice,
        carried_forward_criteria=carried_forward_criteria,
        contract_entries=contract_entries,
        artifact_entries=artifact_entries,
        baseline=baseline,
        omitted=omitted,
        token_count=token_count,
        package_hash=_package_hash(content),
    )

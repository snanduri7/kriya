"""DecisionLedger - MA5.5 of the control-plane implementation plan.

An append-only record of durable ENGINEERING decisions - a triage
classification, a contract change, a policy verdict that actually gated
something - never a duplicate of Kriya's existing logs/traces (kriya/core/
trace.py's TraceLogger already owns the full run trace; this is a much
narrower, denser stream of decisions specifically, the kind a human or a
future audit would want to scan without wading through retry/verification
noise). Persisted as JSON Lines (`.kriya/control/decisions.jsonl`) - one
compact JSON object per line, matching this module's own design doc
examples verbatim.

Field values are kept small on principle (the same "small policy-relevant
facts only, never secrets or full payloads" rule kriya/policy/model.py's
ActionRequest.metadata and kriya/policy/telemetry.py's redaction already
establish) - record() defensively truncates any individual field value
longer than a few thousand characters rather than trusting every future
caller to remember not to pass one. "Never secrets" is ENFORCED at the
file (S20r, OBS-003, I-11): append_to_file() writes each line through the
ONE redactor (kriya/foundation/redaction.py); the in-memory Decision (and
its attempt-evidence mirror) stays as the caller recorded it. Limit: the
in-memory truncation runs first, so a token-shaped secret cut at exactly
the 4,000-character boundary may leave a prefix too short to recognise.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kriya.context.workspace_identity import validate_ledger_line_ownership, workspace_identity
from kriya.control.control_store import write_control_file
from kriya.control.persistence import decision_ledger_path
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.foundation.redaction import redact_value
from kriya.repository.commit import read_file_revision

logger = logging.getLogger(__name__)

_MAX_FIELD_VALUE_CHARS = 4000


# B-004 (owner decision: tolerate and preserve): a line of the audit ledger that cannot be read as a decision is NEVER silently treated as absent and NEVER repaired or rewritten. The reader
# skips it with a structured record, the writer carries its bytes forward verbatim, and the degraded state is reported to whoever asked (the operator's disposition result, the log).
LEDGER_LINE_NOT_UTF8 = "LEDGER_LINE_NOT_UTF8"
LEDGER_LINE_NOT_JSON = "LEDGER_LINE_NOT_JSON"
LEDGER_LINE_NOT_A_DECISION = "LEDGER_LINE_NOT_A_DECISION"


@dataclass(frozen=True)
class MalformedLedgerLine:
    line_number: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"line_number": self.line_number, "reason": self.reason}


@dataclass(frozen=True)
class LedgerAppendResult:
    """What ``append_to_file`` reports besides having written: the malformed lines it carried forward untouched (empty for a healthy ledger)."""

    preserved_malformed_lines: Tuple[MalformedLedgerLine, ...] = ()

    @property
    def degraded(self) -> bool:
        return bool(self.preserved_malformed_lines)


def _classify_ledger_line(workspace_path: str, raw: bytes, source: str) -> Tuple[Optional["Decision"], Optional[str]]:
    """(the Decision, None) for a readable line; (None, reason) for a malformed one. A line stamped for ANOTHER workspace, or carrying no stamp, is not malformed - it is the ownership refusal
    (D2 M4) and raises WorkspaceOwnershipError exactly as it always did."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, LEDGER_LINE_NOT_UTF8
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        return None, LEDGER_LINE_NOT_JSON
    if not isinstance(payload, dict):
        return None, LEDGER_LINE_NOT_A_DECISION
    validate_ledger_line_ownership(workspace_path, payload, source)
    try:
        return Decision.from_dict(payload), None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, LEDGER_LINE_NOT_A_DECISION


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_field_values(fields: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for key, value in fields.items():
        if isinstance(value, str) and len(value) > _MAX_FIELD_VALUE_CHARS:
            result[key] = value[:_MAX_FIELD_VALUE_CHARS] + "...[truncated]"
        else:
            result[key] = value
    return result


@dataclass(frozen=True)
class Decision:
    """type + a flat bag of type-specific fields, matching the design
    doc's own examples verbatim (a flat {"type": "triage", "kind": ...},
    never a nested {"type": ..., "payload": {...}})."""

    type: str
    fields: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        merged = {"type": self.type, "timestamp": self.timestamp}
        merged.update(self.fields)
        return merged

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Decision":
        data = dict(data)
        data.pop("_workspace_id", None)
        data.pop("_run_record", None)
        decision_type = data.pop("type")
        timestamp = data.pop("timestamp", _now_iso())
        return cls(type=decision_type, fields=data, timestamp=timestamp)


class DecisionLedger:
    """In-memory accumulator with explicit append/load against
    .kriya/control/decisions.jsonl (kriya/control/persistence.py's
    decision_ledger_path). Unlike ControlState/ContractRegistry/
    ArtifactRegistry (each a single overwritten JSON document), this store
    is append-only by nature - append_to_file() re-reads the current file,
    adds the new line, and writes the whole thing back through the
    control-store writer (kriya/control/control_store.py, the one atomic
    writer for `.kriya/` state) rather than
    a true O(1) file append, trading a little efficiency for reusing one
    authorized, atomic write path instead of a second bespoke one - decision
    volume within a single Kriya run is small enough that this is not a
    real cost."""

    def __init__(self) -> None:
        self._decisions: List[Decision] = []
        self._malformed_lines: List[MalformedLedgerLine] = []

    @property
    def malformed_lines(self) -> Tuple[MalformedLedgerLine, ...]:
        """The lines of the ledger FILE this ledger was loaded from that could not be read as decisions (preserved on disk, absent from ``all()``)."""
        return tuple(self._malformed_lines)

    @property
    def degraded(self) -> bool:
        return bool(self._malformed_lines)

    def record(self, decision_type: str, **fields: Any) -> Decision:
        decision = Decision(type=decision_type, fields=_truncate_field_values(fields))
        self._decisions.append(decision)
        attempt_evidence_scope.mirror_decision(decision)
        return decision

    def all(self) -> Tuple[Decision, ...]:
        return tuple(self._decisions)

    def filter_by_type(self, decision_type: str) -> Tuple[Decision, ...]:
        return tuple(d for d in self._decisions if d.type == decision_type)

    def append_to_file(self, workspace_path: str, decision: Decision, *, durable: bool = False) -> LedgerAppendResult:
        """Persists ONE decision by rewriting the full decisions.jsonl with
        it appended - see class docstring for why this isn't a true
        streaming append. Loads the existing file's raw lines first (not
        via load_decision_ledger, which reconstructs Decision objects
        losslessly but this only needs the raw text lines to append to). ``durable=True`` (Wave 2 Slice A: a TOOL attempt's outcome and an operator's disposition)
        makes the write fsync-grade through the same control-store writer; the default is the unchanged atomic-only write."""

        path = decision_ledger_path(workspace_path)
        existing_lines: List[bytes] = []
        preserved: List[MalformedLedgerLine] = []
        if os.path.isfile(path):
            with open(path, "rb") as f:
                for line_number, raw in enumerate(f, start=1):
                    if not raw.strip():
                        continue
                    _decision, reason = _classify_ledger_line(workspace_path, raw, f"{path} line {line_number}")
                    if reason is not None:
                        preserved.append(MalformedLedgerLine(line_number, reason))
                    existing_lines.append(raw.rstrip(b"\n"))                       # the exact bytes of every prior line, readable or not
        serialized = redact_value(decision.to_dict())  # S20r (I-11): the stored line is redacted; the Decision is not touched
        serialized["_workspace_id"] = workspace_identity(workspace_path)
        from kriya.control.run_coordinator import current_run_context
        context = current_run_context()
        if context is not None and context.record_revision is not None:
            serialized["_run_record"] = {
                "classification": "derived",
                "run_id": context.run_id,
                "revision": context.record_revision,
            }
        existing_lines.append(json.dumps(serialized, sort_keys=True).encode("utf-8"))
        content_bytes = b"\n".join(existing_lines) + b"\n"

        os.makedirs(os.path.dirname(path), exist_ok=True)
        expected_revision = read_file_revision(path)
        write_control_file(workspace_path, path, content_bytes.decode("utf-8", errors="replace"), expected_revision=expected_revision, durable=durable, content_bytes=content_bytes)
        if preserved:
            logger.warning("Decision ledger %s holds %d malformed line(s) (lines %s); they were preserved byte-for-byte and the new decision was appended after them.",
                           path, len(preserved), ", ".join(str(m.line_number) for m in preserved))
        return LedgerAppendResult(tuple(preserved))

    def record_and_persist(self, workspace_path: str, decision_type: str, **fields: Any) -> Decision:
        decision = self.record(decision_type, **fields)
        self.append_to_file(workspace_path, decision)
        return decision


def load_decision_ledger(workspace_path: str) -> DecisionLedger:
    """Tolerant line-by-line load: a malformed trailing line (e.g. from a
    process killed mid-append before commit_file's own atomic replace could
    even run) is skipped and logged, never invalidating every decision
    recorded before it - unlike the single-document stores, one bad line
    here does not mean the whole store is corrupt."""

    ledger = DecisionLedger()
    path = decision_ledger_path(workspace_path)
    if not os.path.isfile(path):
        return ledger
    with open(path, "rb") as f:
        for line_number, raw in enumerate(f, start=1):
            if not raw.strip():
                continue
            decision, reason = _classify_ledger_line(workspace_path, raw, f"{path} line {line_number}")
            if reason is not None:
                ledger._malformed_lines.append(MalformedLedgerLine(line_number, reason))
                logger.warning("Skipping malformed decision-ledger line %d in %s (%s); it is preserved on disk, never repaired", line_number, path, reason)
                continue
            ledger._decisions.append(decision)
    return ledger

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
caller to remember not to pass one.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from kriya.control.persistence import decision_ledger_path
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow.edit_safety import read_file_revision

logger = logging.getLogger(__name__)

_MAX_FIELD_VALUE_CHARS = 4000


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
        decision_type = data.pop("type")
        timestamp = data.pop("timestamp", _now_iso())
        return cls(type=decision_type, fields=data, timestamp=timestamp)


class DecisionLedger:
    """In-memory accumulator with explicit append/load against
    .kriya/control/decisions.jsonl (kriya/control/persistence.py's
    decision_ledger_path). Unlike ControlState/ContractRegistry/
    ArtifactRegistry (each a single overwritten JSON document), this store
    is append-only by nature - append_to_file() re-reads the current file,
    adds the new line, and writes the whole thing back through
    AuthorizedFileWriter (the same real containment/sensitive-path
    enforcement every other control-plane write goes through) rather than
    a true O(1) file append, trading a little efficiency for reusing one
    authorized, atomic write path instead of a second bespoke one - decision
    volume within a single Kriya run is small enough that this is not a
    real cost."""

    def __init__(self) -> None:
        self._decisions: List[Decision] = []

    def record(self, decision_type: str, **fields: Any) -> Decision:
        decision = Decision(type=decision_type, fields=_truncate_field_values(fields))
        self._decisions.append(decision)
        return decision

    def all(self) -> Tuple[Decision, ...]:
        return tuple(self._decisions)

    def filter_by_type(self, decision_type: str) -> Tuple[Decision, ...]:
        return tuple(d for d in self._decisions if d.type == decision_type)

    def append_to_file(self, workspace_path: str, decision: Decision) -> None:
        """Persists ONE decision by rewriting the full decisions.jsonl with
        it appended - see class docstring for why this isn't a true
        streaming append. Loads the existing file's raw lines first (not
        via load_decision_ledger, which reconstructs Decision objects
        losslessly but this only needs the raw text lines to append to)."""

        path = decision_ledger_path(workspace_path)
        existing_lines: List[str] = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                existing_lines = [line.rstrip("\n") for line in f if line.strip()]
        existing_lines.append(json.dumps(decision.to_dict(), sort_keys=True))
        content = "\n".join(existing_lines) + "\n"

        os.makedirs(os.path.dirname(path), exist_ok=True)
        expected_revision = read_file_revision(path)
        AuthorizedFileWriter(workspace_path).commit_file(path, content, expected_revision=expected_revision)

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
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                ledger._decisions.append(Decision.from_dict(json.loads(line)))
            except Exception:
                logger.warning("Skipping malformed decision-ledger line %d in %s", line_number, path, exc_info=True)
    return ledger

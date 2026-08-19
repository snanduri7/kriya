"""Canonical local evidence records.

These records may contain proprietary paths, diagnostics, and source. They are
for local trace persistence only and are intentionally incompatible with the
sanitized outward-lookup request type.
"""
from dataclasses import asdict, dataclass, field
import time
from typing import Any, Dict


@dataclass(frozen=True)
class EvidenceRecord:
    kind: str
    source: str
    attempt: int
    payload: Dict[str, Any]
    sensitivity: str = "local_only"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

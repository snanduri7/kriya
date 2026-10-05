"""Schema vocabulary of the attempt evidence store (``kriya.attempt_evidence/1``).

LR-R1-M1 design §3. Pure data: no I/O, no imports from the workflow.
Every record shares one envelope; ``kind`` selects the payload. Content
(prompts, responses, source, gate output) never sits in the envelope: it is
a content-addressed blob (capture ``full``) or a digest + length only
(``digest_only``), named under ``content_digests``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

SCHEMA = "kriya.attempt_evidence/1"
SUPPORTED_SCHEMAS = (SCHEMA,)
WRITER_VERSION = "1"
REDACTION_POLICY_VERSION = "1"

# Provenance of a record's values (design §3.2). LEGACY_RECONSTRUCTED is used
# only by the out-of-tree legacy importer (M1.11).
OBSERVED = "OBSERVED"
DERIVED = "DERIVED"
MODEL_CLAIMED = "MODEL_CLAIMED"
MIRRORED = "MIRRORED"
NOT_RECORDED = "NOT_RECORDED"
LEGACY_RECONSTRUCTED = "LEGACY_RECONSTRUCTED"
PROVENANCES = frozenset({OBSERVED, DERIVED, MODEL_CLAIMED, MIRRORED, NOT_RECORDED, LEGACY_RECONSTRUCTED})

# Capture modes (design §7; D4: default full).
CAPTURE_FULL = "full"
CAPTURE_DIGEST_ONLY = "digest_only"
CAPTURE_FULL_WITH_REASONING = "full_with_reasoning"
CAPTURE_OFF = "off"
CAPTURE_MODES = (CAPTURE_FULL, CAPTURE_DIGEST_ONLY, CAPTURE_FULL_WITH_REASONING, CAPTURE_OFF)

# Runtime diagnosis evidence classes (design §14 D2) - never TRACED/CONFIRMED,
# which are human adjudication labels.
EVIDENCE_CLASSES = ("MEASURED", "DERIVED_DETERMINISTIC", "MODEL_CLAIMED", "UNKNOWN")

KINDS = frozenset({
    "run.opened", "run.closed",
    "unit.opened", "unit.closed",
    "phase.opened", "phase.closed",
    "attempt.opened", "attempt.closed", "attempt.concluded",
    "model.request", "model.response", "model.result",
    "prompt.sections",
    "authority.snapshot",
    "developer.parse",
    "candidate.change",
    "gate.result",
    "obligations.snapshot",
    "diagnosis",
    "recovery.decision",
    "fallback.decision",
    "tool.execution",
    "retry.delta",
    "mirror.event", "mirror.decision", "mirror.evidence",
    "mirror.gate_outcome", "mirror.gate_outcomes_restored",
    "recorder.gap",
    "not_recorded",
})

# Envelope keys in a fixed order (readability only; canonical bytes sort keys).
ENVELOPE_KEYS = (
    "schema", "seq", "prev", "kind", "run_id", "unit_id", "unit_kind", "invocation_seq", "phase",
    "attempt_number", "call_seq", "wire_seq", "role", "t_wall", "t_mono_ms", "provenance", "payload",
    "content_digests", "blobs",
)

GENESIS_PREV = "sha256:" + "0" * 64


def canonical_bytes(record: Mapping[str, Any]) -> bytes:
    """The bytes a record's digest covers: sorted keys, no whitespace, UTF-8."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def record_digest(record: Mapping[str, Any]) -> str:
    return digest(canonical_bytes(record))


def as_bytes(value: Any) -> bytes:
    """Content to exact bytes: str as UTF-8, bytes unchanged, anything else as
    canonical JSON (an exact, reproducible serialization of a structure)."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    # Content mirrors existing objects as they are; a value JSON cannot
    # encode natively is rendered with str() rather than dropped.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def content_digest_entry(data: bytes) -> Dict[str, Any]:
    return {"digest": digest(data), "bytes": len(data)}

"""The KUP envelopes, the capabilities description, the source/consistency blocks and the adapter's own argument validation.

Since full modularization D2 the write-free queries of a published snapshot (history list/detail/prompt, the cursors, the display goal)
are STATE's snapshot reader, ``kriya.control.history.SnapshotTrace``, reached through ``client.history``; nothing here opens a database.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from typing import Any, Dict, Optional

from kriya.foundation.ids import RUN_ID_RULE, is_valid_run_id
from kriya.foundation.redaction import redact_text
from kriya.kup import policy
from kriya.kup.store import SnapshotError

# The opaque-cursor token shape the KUP adapter accepts (evidence.records; the history cursors are STATE's, kriya/control/history).
CURSOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+/=_-]*$")


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------ envelopes

def envelope(operation: str, request_id: str, *, data: Any = None, error: Optional[Dict[str, Any]] = None,
             source: Optional[Dict[str, Any]] = None, consistency: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "schema_version": policy.KUP_SCHEMA_VERSION,
        "operation": operation,
        "request_id": request_id,
        "observed_at": utc_now(),
        "source": None if error else source,
        "consistency": None if error else consistency,
        "data": None if error else data,
        "error": error,
    }


def error_envelope(operation: str, request_id: str, code: str, message: str, database_state: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    # S2: an error message can echo a configured value (a URL with userinfo, a pydantic input_value); the envelope carries it redacted.
    err: Dict[str, Any] = {"code": code, "message": redact_text(message), "database_state": database_state}
    err.update(extra)
    return envelope(operation, request_id, error=err)


def from_snapshot_error(operation: str, request_id: str, error: SnapshotError) -> Dict[str, Any]:
    return error_envelope(operation, request_id, error.code, error.message, error.database_state, **error.extra)


def source_block(state_dir: str, trace_db: str, snapshot: Any = None) -> Dict[str, Any]:
    block: Dict[str, Any] = {"state_directory": state_dir, "trace_database": trace_db}
    if snapshot is not None:
        block["snapshot_directory"] = snapshot.directory
        block["snapshot_id"] = snapshot.snapshot_id
    return block


def snapshot_consistency(snapshot_id: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    """gate C-3: one committed image via SQLite backup; acquisition start/completion recorded; source metadata
    compared by stat only - a difference supports 'source metadata change detected', equality establishes nothing.
    ``summary`` is the snapshot's summary with the source metadata observed now (``client.history.summary``)."""
    return {
        "kind": policy.CONSISTENCY_SNAPSHOT_COPY,
        "live_stream": False,
        "snapshot_id": snapshot_id,
        "acquisition_started_at": summary["acquisition_started_at"],
        "acquisition_completed_at": summary["acquisition_completed_at"],
        "source_metadata_at_acquisition": summary["source_metadata_at_acquisition"],
        "source_metadata_now": summary["source_metadata_now"],
        "source_metadata_changed": summary["source_metadata_changed"],
    }


def live_consistency() -> Dict[str, Any]:
    return {"kind": policy.CONSISTENCY_LIVE_OBSERVATION, "live_stream": False}


def not_applicable_consistency() -> Dict[str, Any]:
    return {"kind": policy.CONSISTENCY_NOT_APPLICABLE, "live_stream": False}


def encode_response(env: Dict[str, Any]) -> str:
    """Serialize; an envelope above the response limit becomes a typed RESPONSE_TOO_LARGE error, never a partial body."""
    text = json.dumps(env, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text.encode("utf-8")) > policy.MAX_RESPONSE_BYTES:
        replacement = error_envelope(env.get("operation", "unknown"), env.get("request_id", ""), policy.RESPONSE_TOO_LARGE,
                                     f"the response would exceed {policy.MAX_RESPONSE_BYTES} bytes", bytes=len(text.encode("utf-8")))
        return json.dumps(replacement, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text


# ------------------------------------------------------------------ capabilities

def capabilities(health) -> Dict[str, Any]:
    """The KUP wire description of this build: the protocol's own vocabulary (operations, limits, codes, flags) around the Kriya facts
    ``HealthService.capabilities`` reports (``health``: build identity, the content-visibility decision, the evidence vocabulary)."""
    return {
        "kup_versions": [policy.KUP_SCHEMA_VERSION],
        "operations": list(policy.OPERATIONS),
        "identity": {"kriya_version": health.kriya_version, "commit": health.commit, "build_provenance": health.build_provenance,
                     "implementation": policy.PROTOCOL_IMPLEMENTATION, "sqlite_version": sqlite3.sqlite_version},
        "limits": dict(policy.LIMITS),
        "features": {"snapshot": True, "prompt": True, "comparisons": False, "attribution": False, "diagnostics": False, "output": False,
                     "consistency_kinds": list(policy.CONSISTENCY_KINDS), "error_codes": list(policy.ERROR_CODES), "database_states": list(policy.DATABASE_STATES),
                     **_cockpit_features(health)},
    }


def _cockpit_features(health) -> Dict[str, Any]:
    """Capability set 1.1 flags: true only for what this build implements (a flag is never advertised before its operation exists)."""
    content_authorized = health.content_authorized  # Kriya's own SECURITY_AUTHORITY decision (HealthService), never a host's
    return {
        "capability_set": policy.CAPABILITY_SET,
        "evidence": True, "evidence_content": content_authorized, "evidence_content_classes": sorted(set(policy.BLOB_CLASSES.values())) if content_authorized else [],
        "qualification": False, "authority": False,
        "knowledge": False, "tools": False, "experiments": False, "live": False, "actions": False,
        "workspace_create": True, "skills_view": True, "tools_view": True, "model_probe": True, "skills_catalog": True, "tools_catalog": True,
        "evidence_capture_modes": list(health.evidence_capture_modes),
        "evidence_verification_states": list(policy.EVIDENCE_VERIFICATION_STATES),
        "evidence_kinds": list(health.evidence_kinds),
        "evidence_provenances": list(health.evidence_provenances),
        "diagnosis_evidence_classes": list(health.diagnosis_evidence_classes),
    }


# ------------------------------------------------------------------ argument validation

def validate_run_id(run_id: Any) -> str:
    if not is_valid_run_id(run_id):
        raise SnapshotError(policy.INVALID_REQUEST, RUN_ID_RULE)
    return run_id

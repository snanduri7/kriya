"""Live local-model certification records (PRD-035).

A certification says: Kriya, with this exact Developer runtime (the PRD-013
fingerprint digest) and these exact inference settings (the PRD-014
settings digest), on this execution environment (the environment digest),
passed every required case of this case-set version of the live
certification matrix (tests/test_live_prd035_certification.py, run by
scripts/certify_model.sh). It is evidence about that identity only: a
changed runtime, settings, environment or case set makes it STALE. A matrix
that did not pass every case is stored FAILED, never CURRENT.

Records live outside every workspace (``~/.kriya/certifications/``,
``KRIYA_CERTIFICATION_HOME``), like qualification records. Each is sealed by
a digest of its body; a tampered record is INVALID and never counts.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

CERTIFICATION_HOME_ENV = "KRIYA_CERTIFICATION_HOME"
RECORD_SCHEMA_VERSION = 1
# The live certification matrix's case set; bump when a case is added,
# removed or its success evidence changes (every earlier record goes STALE).
CASE_SET_VERSION = 1

CERTIFIED, FAILED = "CERTIFIED", "FAILED"
CURRENT, STALE, MISSING, INVALID = "CURRENT", "STALE", "MISSING", "INVALID"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CertificationKey:
    model: str
    runtime_digest: str
    inference_settings_digest: str
    environment_digest: str
    case_set_version: int = CASE_SET_VERSION

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


def certification_home() -> str:
    configured = os.environ.get(CERTIFICATION_HOME_ENV)
    home = os.path.expanduser(configured) if configured else os.path.join(os.path.expanduser("~"), ".kriya", "certifications")
    return os.path.realpath(home)


def record_path(key: CertificationKey) -> str:
    return os.path.join(certification_home(), f"{key.digest}.json")


def save_certification(key: CertificationKey, report: Mapping[str, Any], *, now: Optional[datetime] = None) -> str:
    """Store the matrix result for ``key`` (CERTIFIED only when every
    required case passed). ``report`` is the content-digested matrix report."""
    content = report["content"]
    status = CERTIFIED if content.get("status") == CERTIFIED else FAILED
    body = {
        "schema_version": RECORD_SCHEMA_VERSION, "key": asdict(key), "status": status,
        "report_digest": report["content_digest"],
        "cases": [{"case_id": c["case_id"], "verdict": c["verdict"]} for c in content.get("cases", [])],
        "recorded_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    path = record_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({**body, "record_digest": _digest(body)}, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def _load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        body = {k: v for k, v in record.items() if k != "record_digest"}
        if record.get("schema_version") != RECORD_SCHEMA_VERSION or record.get("record_digest") != _digest(body):
            return None
        return record
    except (OSError, ValueError, AttributeError):
        return None


def certification_status(key: CertificationKey) -> Dict[str, Any]:
    """CURRENT (a CERTIFIED record for exactly this key), FAILED (the matrix
    ran for this key and did not pass), INVALID (the record is tampered or
    unreadable), STALE (a record exists for this model, but for another
    runtime, settings, environment or case set) or MISSING."""
    path = record_path(key)
    if os.path.exists(path):
        record = _load(path)
        if record is None:
            return {"status": INVALID, "path": path}
        return {"status": CURRENT if record["status"] == CERTIFIED else FAILED, "path": path,
                "report_digest": record["report_digest"], "recorded_at": record["recorded_at"]}
    others: List[Dict[str, Any]] = []
    home = certification_home()
    if os.path.isdir(home):
        for name in sorted(os.listdir(home)):
            if name.endswith(".json"):
                record = _load(os.path.join(home, name))
                if record is not None and record["key"].get("model") == key.model:
                    others.append(record["key"])
    if others:
        changed = sorted({field for other in others for field, value in other.items()
                          if field != "model" and asdict(key).get(field) != value})
        return {"status": STALE, "changed": changed}
    return {"status": MISSING}

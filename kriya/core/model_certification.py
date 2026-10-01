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


# --- PRD-036 release streak (LIVE-CERTIFICATION-REPEATED-TRIALS-001) -----------
#
# A release needs RELEASE_STREAK_REQUIRED consecutive complete matrices, each
# passing every case, on one unchanged release-candidate identity
# (kriya/core/release_candidate.py). Every trial, passing or failing, is
# appended to a sealed log keyed by the candidate digest, and nothing is ever
# overwritten or dropped. A failed or incomplete matrix, or one that ran on
# another identity, resets the streak to 0. A different candidate has its own
# log, so it starts at 0. The PRD-035 record above keeps its meaning
# ("the latest full matrix passed").

STREAK_SCHEMA_VERSION = 1
RELEASE_STREAK_REQUIRED = 3
TARGET_TIER = "target_production"


class StreakRecordInvalid(RuntimeError):
    """The streak log exists but is tampered with or unreadable. It is never
    overwritten: an operator must look at it."""


def streak_path(candidate_digest: str) -> str:
    return os.path.join(certification_home(), "streaks", f"{candidate_digest}.json")


def _content_digest(content: Any) -> str:
    # The report's own digest (tests/_model_certification.py::build_report).
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def _load_streak(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": STREAK_SCHEMA_VERSION, "trials": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            log = json.load(handle)
        body = {k: v for k, v in log.items() if k != "log_digest"}
        if log.get("schema_version") == STREAK_SCHEMA_VERSION and log.get("log_digest") == _digest(body):
            return body
    except (OSError, ValueError, AttributeError):
        pass
    raise StreakRecordInvalid(f"release streak log {path} is tampered with or unreadable")


def trial_failures(candidate: Mapping[str, Any], report: Mapping[str, Any]) -> List[str]:
    """Why a matrix does not count as a passing trial for ``candidate``; an
    empty list means it passes."""
    content = report.get("content") or {}
    reasons: List[str] = []
    if report.get("content_digest") != _content_digest(content):
        reasons.append("report_digest_mismatch")
    if content.get("tier") != TARGET_TIER:
        reasons.append("not_target_tier")
    if content.get("status") != CERTIFIED:
        reasons.append("matrix_not_certified")
    cases = content.get("cases") or []
    expected = list((candidate.get("case_set") or {}).get("case_ids") or [])
    if [c.get("case_id") for c in cases] != expected or not expected:
        reasons.append("case_set_mismatch")
    reasons += [f"case_{c.get('case_id')}_{c.get('verdict')}" for c in cases if c.get("verdict") != "PASSED"]
    if content.get("case_set_version") != (candidate.get("case_set") or {}).get("version"):
        reasons.append("case_set_version_mismatch")
    models = candidate.get("models") or {}
    primary = models.get("developer_primary") or {}
    identity = content.get("identity") or {}
    if (identity.get("model"), identity.get("runtime_fingerprint"), identity.get("inference_settings_digest")) != (
            primary.get("model"), primary.get("runtime_digest"), primary.get("inference_settings_digest")):
        reasons.append("primary_identity_mismatch")
    if (content.get("execution_environment") or {}).get("digest") != (models.get("execution_environment") or {}).get("digest"):
        reasons.append("environment_mismatch")
    fallback = models.get("developer_fallback")
    fallback_case = next((c for c in cases if c.get("task_class") == "fallback_transition"), None)
    if fallback_case is not None:
        developer = ((fallback_case.get("roles") or {}).get("developer") or {})
        if (not fallback or fallback.get("runtime_digest") not in (developer.get("runtime_digests") or [])
                or fallback.get("inference_settings_digest") not in (developer.get("settings_digests") or [])):
            reasons.append("fallback_identity_mismatch")
    return reasons


def record_trial(candidate: Mapping[str, Any], report: Mapping[str, Any], *, started: str, ended: str,
                 evidence: str, candidate_after: Optional[Mapping[str, Any]] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Append one matrix trial to ``candidate``'s streak log and return the
    entry. ``candidate_after`` is the identity taken again when the matrix
    ended; if it differs, the trial fails (the identity changed mid-streak)."""
    from kriya.core.release_candidate import candidate_digest_valid

    if not candidate_digest_valid(candidate):
        raise ValueError("the release-candidate identity's digest does not match its fields")
    path = streak_path(candidate["digest"])
    log = _load_streak(path)
    reasons = trial_failures(candidate, report)
    if candidate_after is not None and candidate_after.get("digest") != candidate["digest"]:
        reasons.append("identity_changed_during_trial")
    trials = log["trials"]
    previous = trials[-1]["streak_after"] if trials else 0
    content = report.get("content") or {}
    entry = {
        "trial": len(trials) + 1, "started": started, "ended": ended,
        "revision": ((candidate.get("release") or {}).get("kriya") or {}).get("revision"),
        "candidate_digest": candidate["digest"],
        "case_set_version": content.get("case_set_version"),
        "cases": [{"case_id": c.get("case_id"), "verdict": c.get("verdict")} for c in content.get("cases") or []],
        "report_digest": report.get("content_digest"),
        "outcome": CERTIFIED if not reasons else FAILED, "reasons": reasons,
        "streak_after": previous + 1 if not reasons else 0,
        "evidence": evidence,
        "recorded_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    body = {"schema_version": STREAK_SCHEMA_VERSION, "candidate_digest": candidate["digest"],
            "trials": [*trials, entry]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({**body, "log_digest": _digest(body)}, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return entry


def streak_status(candidate_digest: str) -> Dict[str, Any]:
    """CERTIFIED once the latest RELEASE_STREAK_REQUIRED trials all passed;
    IN_PROGRESS below that, MISSING with no trial, INVALID if tampered with."""
    path = streak_path(candidate_digest)
    try:
        log = _load_streak(path)
    except StreakRecordInvalid:
        return {"status": INVALID, "path": path, "streak": 0, "trials": []}
    trials = log["trials"]
    streak = trials[-1]["streak_after"] if trials else 0
    status = MISSING if not trials else CERTIFIED if streak >= RELEASE_STREAK_REQUIRED else "IN_PROGRESS"
    return {"status": status, "path": path, "streak": streak, "required": RELEASE_STREAK_REQUIRED,
            "trials": trials}

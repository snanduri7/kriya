"""Trusted risk acceptance: the static-analysis waiver registry (PRD-031A §9).

A waiver releases one blocking finding class, only when every bound field
matches. It is created and revoked only by the operator CLI
(``kriya static-analysis waive|revoke``): ``write_waiver``/``revoke_waiver``
have exactly one production caller each (tests/test_prd031a_static_analysis.py).
Nothing a model, a repository or a candidate produces is an input to
matching, which reads only the store and the normalized finding.

The store lives OUTSIDE the workspace (``~/.kriya/static_analysis/waivers/
<workspace_id>.json``, ``KRIYA_STATIC_ANALYSIS_HOME`` override, or an
explicit operator path that must also resolve outside it), so a repository
can never ship its own risk acceptance - the SEC-009 P2 / TOOL-002 P2
pattern. It is read fresh on every evaluation: a revoke takes effect on the
next gate. Coverage gaps and scanner failures are never waivable.
"""

from __future__ import annotations

import fnmatch
import getpass
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.config.authority_approval import validate_trust_path_outside_workspace
from kriya.control.workspace_identity import workspace_identity
from kriya.static_analysis.model import (
    WAIVER_DIGEST_INVALID,
    WAIVER_EXPIRED,
    WAIVER_RULE_PACK_MISMATCH,
    WAIVER_SCOPE_MISMATCH,
    WAIVER_SEVERITY_EXCEEDED,
    WAIVER_WORKSPACE_MISMATCH,
    Classification,
    ClassifiedFinding,
    Severity,
    canonical_digest,
)

ENV_HOME_OVERRIDE = "KRIYA_STATIC_ANALYSIS_HOME"
STORE_SCHEMA_VERSION = 1
DISPOSITION_ACCEPTED_RISK = "accepted_risk"
_WAIVABLE_CLASSIFICATIONS = frozenset({
    Classification.INTRODUCED.value, Classification.WORSENED.value, "existing",
})


class WaiverStoreError(ValueError):
    """The store is unreadable, corrupt, tampered with or of an unknown
    schema: no waiver from it applies."""


@dataclass(frozen=True)
class WaiverRecord:
    waiver_id: str
    provider: str
    rule_id: str
    paths: Tuple[str, ...]
    reason: str
    owner: str
    max_severity: str
    provenance: Mapping[str, str]
    classifications: Tuple[str, ...] = ("existing",)
    fingerprint: Optional[str] = None
    # Reserved for dependency (SCA) providers; validated, not applied in v1.
    package: Optional[str] = None
    version_range: Optional[str] = None
    rule_pack_digest: Optional[str] = None
    tracking_ref: Optional[str] = None
    expires_at: Optional[str] = None
    disposition: str = DISPOSITION_ACCEPTED_RISK
    record_digest: str = field(default="", compare=False)

    def body(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("record_digest")
        data["paths"] = list(self.paths)
        data["classifications"] = list(self.classifications)
        data["provenance"] = dict(self.provenance)
        return data

    def computed_digest(self) -> str:
        return canonical_digest(self.body())

    def to_dict(self) -> Dict[str, Any]:
        return {**self.body(), "record_digest": self.record_digest}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"expires_at {value!r} has no timezone")
    return parsed


def _validate_record(record: WaiverRecord) -> None:
    if not record.waiver_id or not record.rule_id or not record.provider:
        raise WaiverStoreError("a waiver needs waiver_id, provider and rule_id")
    if not record.paths:
        raise WaiverStoreError(f"waiver {record.waiver_id}: at least one path scope is required")
    if not record.reason.strip() or not record.owner.strip():
        raise WaiverStoreError(f"waiver {record.waiver_id}: reason and owner are required")
    if record.disposition != DISPOSITION_ACCEPTED_RISK:
        raise WaiverStoreError(f"waiver {record.waiver_id}: disposition must be accepted_risk")
    if record.max_severity not in {s.value for s in Severity if s is not Severity.UNKNOWN}:
        raise WaiverStoreError(f"waiver {record.waiver_id}: invalid max_severity {record.max_severity!r}")
    if not record.classifications or not set(record.classifications) <= _WAIVABLE_CLASSIFICATIONS:
        raise WaiverStoreError(f"waiver {record.waiver_id}: invalid classifications {record.classifications!r}")
    if (record.package is None) != (record.version_range is None):
        raise WaiverStoreError(f"waiver {record.waiver_id}: package and version_range are set together")
    if record.expires_at is not None:
        try:
            _parse_time(record.expires_at)
        except ValueError as error:
            raise WaiverStoreError(f"waiver {record.waiver_id}: {error}") from error
    for key in ("created_at", "created_by", "created_via", "workspace_id"):
        if not record.provenance.get(key):
            raise WaiverStoreError(f"waiver {record.waiver_id}: provenance.{key} is required")


def _record_from_dict(data: Mapping[str, Any]) -> WaiverRecord:
    try:
        record = WaiverRecord(
            waiver_id=data["waiver_id"], provider=data["provider"], rule_id=data["rule_id"],
            paths=tuple(data["paths"]), reason=data["reason"], owner=data["owner"],
            max_severity=data["max_severity"], provenance=dict(data["provenance"]),
            classifications=tuple(data.get("classifications") or ("existing",)),
            fingerprint=data.get("fingerprint"), package=data.get("package"),
            version_range=data.get("version_range"), rule_pack_digest=data.get("rule_pack_digest"),
            tracking_ref=data.get("tracking_ref"), expires_at=data.get("expires_at"),
            disposition=data.get("disposition", DISPOSITION_ACCEPTED_RISK),
            record_digest=data["record_digest"],
        )
    except (KeyError, TypeError) as error:
        raise WaiverStoreError(f"malformed waiver record: {error!r}") from error
    unknown = set(data) - set(record.to_dict())
    if unknown:
        raise WaiverStoreError(f"waiver {record.waiver_id}: unknown fields {sorted(unknown)}")
    _validate_record(record)
    if record.computed_digest() != record.record_digest:
        raise WaiverStoreError(f"waiver {record.waiver_id}: {WAIVER_DIGEST_INVALID} (record was modified)")
    return record


# --- Store location ------------------------------------------------------

def waiver_store_path(configured: Optional[str], workspace_root: str) -> str:
    """The store path; always outside the workspace (TrustPathInsideWorkspaceError otherwise)."""
    if configured:
        path = os.path.realpath(os.path.expanduser(configured))
    else:
        home = os.environ.get(ENV_HOME_OVERRIDE) or os.path.join(os.path.expanduser("~"), ".kriya", "static_analysis", "waivers")
        path = os.path.join(os.path.realpath(home), f"{workspace_identity(workspace_root)}.json")
    validate_trust_path_outside_workspace(path, workspace_root)
    return path


@dataclass(frozen=True)
class WaiverStore:
    path: str
    status: str                       # "valid" | "absent" | "invalid"
    records: Tuple[WaiverRecord, ...] = ()
    error: Optional[str] = None


def load_waivers(path: str, workspace_root: str) -> WaiverStore:
    """Read the store fresh. Any corruption invalidates the whole store:
    nothing from a tampered file applies."""
    if not os.path.exists(path):
        return WaiverStore(path=path, status="absent")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("schema_version") != STORE_SCHEMA_VERSION:
            raise WaiverStoreError(f"unknown waiver store schema {payload.get('schema_version') if isinstance(payload, dict) else None!r}")
        if payload.get("workspace_id") != workspace_identity(workspace_root):
            raise WaiverStoreError("the waiver store belongs to a different workspace")
        records = tuple(_record_from_dict(item) for item in payload.get("waivers", []))
        ids = [r.waiver_id for r in records]
        if len(ids) != len(set(ids)):
            raise WaiverStoreError("duplicate waiver ids")
    except (OSError, ValueError) as error:
        return WaiverStore(path=path, status="invalid", error=f"{type(error).__name__}: {error}")
    return WaiverStore(path=path, status="valid", records=records)


def is_expired(record: WaiverRecord, now: Optional[datetime] = None) -> bool:
    return record.expires_at is not None and _parse_time(record.expires_at) <= (now or datetime.now(timezone.utc))


# --- Matching --------------------------------------------------------------

@dataclass(frozen=True)
class WaiverRejection:
    waiver_id: str
    fingerprint: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {"waiver_id": self.waiver_id, "fingerprint": self.fingerprint, "reason": self.reason}


def _binding_key(record: WaiverRecord) -> Tuple[str, str]:
    """The rule a waiver names: an opaque (provider, rule id) key, compared
    for equality only - never a branch on which provider it is."""
    return record.provider, record.rule_id


def _finding_key(item: ClassifiedFinding) -> Tuple[str, str]:
    return item.finding.provider, item.finding.rule_id


def _classification_key(item: ClassifiedFinding) -> str:
    return "existing" if item.classification is Classification.UNCHANGED else item.classification.value


def _mismatch(
    record: WaiverRecord, item: ClassifiedFinding, *, now: datetime, workspace_id: str,
    rule_pack_digests: Sequence[str],
) -> Optional[str]:
    """The first bound field that does not match, or None when the waiver applies."""
    if record.provenance.get("workspace_id") != workspace_id:
        return WAIVER_WORKSPACE_MISMATCH
    if is_expired(record, now):
        return WAIVER_EXPIRED
    if not any(fnmatch.fnmatchcase(item.finding.path, pattern) for pattern in record.paths):
        return WAIVER_SCOPE_MISMATCH
    if record.fingerprint is not None and record.fingerprint != item.fingerprint:
        return WAIVER_SCOPE_MISMATCH
    if _classification_key(item) not in record.classifications:
        return WAIVER_SCOPE_MISMATCH
    if item.finding.severity.decided_as.rank > Severity(record.max_severity).rank:
        return WAIVER_SEVERITY_EXCEEDED
    if record.rule_pack_digest is not None and record.rule_pack_digest not in rule_pack_digests:
        return WAIVER_RULE_PACK_MISMATCH
    return None


def match_waivers(
    blocked: Sequence[ClassifiedFinding], store: WaiverStore, *, workspace_root: str,
    rule_pack_digests: Sequence[str], now: Optional[datetime] = None,
) -> Tuple[Dict[str, WaiverRecord], Tuple[WaiverRejection, ...]]:
    """Waivers for findings that would block. Returns fingerprint -> the
    applied waiver, and every near miss (same provider and rule) with the
    reason it did not apply."""
    if store.status != "valid":
        return {}, ()
    now = now or datetime.now(timezone.utc)
    workspace_id = workspace_identity(workspace_root)
    applied: Dict[str, WaiverRecord] = {}
    rejections: List[WaiverRejection] = []
    for item in blocked:
        for record in store.records:
            if _binding_key(record) != _finding_key(item):
                continue
            reason = _mismatch(record, item, now=now, workspace_id=workspace_id, rule_pack_digests=rule_pack_digests)
            if reason is None:
                applied[item.fingerprint] = record
                break
            rejections.append(WaiverRejection(record.waiver_id, item.fingerprint, reason))
    return applied, tuple(rejections)


# --- Operator writes (CLI only) ---------------------------------------------

def _kriya_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("kriya")
    except PackageNotFoundError:
        return "unknown"


def new_waiver(
    *, workspace_root: str, waiver_id: str, provider: str, rule_id: str, paths: Sequence[str],
    reason: str, owner: str, max_severity: str, classifications: Sequence[str] = ("existing",),
    fingerprint: Optional[str] = None, rule_pack_digest: Optional[str] = None,
    tracking_ref: Optional[str] = None, expires_at: Optional[str] = None,
) -> WaiverRecord:
    record = WaiverRecord(
        waiver_id=waiver_id, provider=provider, rule_id=rule_id, paths=tuple(paths), reason=reason,
        owner=owner, max_severity=max_severity, classifications=tuple(classifications),
        fingerprint=fingerprint, rule_pack_digest=rule_pack_digest, tracking_ref=tracking_ref,
        expires_at=expires_at,
        provenance={
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "created_by": getpass.getuser(),
            "created_via": "cli:kriya static-analysis waive",
            "workspace_id": workspace_identity(workspace_root),
            "kriya_version": _kriya_version(),
        },
    )
    _validate_record(record)
    return WaiverRecord(**{**record.__dict__, "record_digest": record.computed_digest()})


def _writable_records(path: str, workspace_root: str) -> List[WaiverRecord]:
    store = load_waivers(path, workspace_root)
    if store.status == "invalid":
        raise WaiverStoreError(f"refusing to modify an invalid waiver store ({store.error}); repair or remove {path}")
    return list(store.records)


def _save(path: str, workspace_root: str, records: Sequence[WaiverRecord]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "schema_version": STORE_SCHEMA_VERSION, "workspace_id": workspace_identity(workspace_root),
        "waivers": [r.to_dict() for r in records],
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def write_waiver(path: str, workspace_root: str, record: WaiverRecord) -> None:
    """Operator CLI only (``kriya static-analysis waive``)."""
    records = _writable_records(path, workspace_root)
    if any(r.waiver_id == record.waiver_id for r in records):
        raise WaiverStoreError(f"waiver {record.waiver_id} already exists")
    _save(path, workspace_root, [*records, record])


def revoke_waiver(path: str, workspace_root: str, waiver_id: str) -> bool:
    """Operator CLI only (``kriya static-analysis revoke``). False when absent."""
    records = _writable_records(path, workspace_root)
    kept = [r for r in records if r.waiver_id != waiver_id]
    if len(kept) == len(records):
        return False
    _save(path, workspace_root, kept)
    return True

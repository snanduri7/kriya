"""Provider-neutral static-analysis types (PRD-031A).

Every type a provider, the policy, the service or a caller exchanges. No
provider is named here; an adapter normalizes into these types before
anything above it sees a result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple


def canonical_digest(value: Any) -> str:
    """sha256 over canonical JSON: the one digest primitive of this package."""
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def bytes_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    # A provider severity the adapter could not map. Decided as HIGH.
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def decided_as(self) -> "Severity":
        """The severity the policy applies: unknown is decided as high."""
        return Severity.HIGH if self is Severity.UNKNOWN else self


_SEVERITY_RANK = {
    Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
    Severity.HIGH: 3, Severity.UNKNOWN: 3, Severity.CRITICAL: 4,
}


class Classification(str, Enum):
    INTRODUCED = "introduced"
    UNCHANGED = "unchanged"
    WORSENED = "worsened"
    RESOLVED = "resolved"


class ScanScope(str, Enum):
    """The scope a provider needs for trustworthy analysis, narrowest first."""

    CHANGED_FILES = "changed_files"
    MODULE = "module"
    REPOSITORY = "repository"
    BUILD_GRAPH = "build_graph"

    @property
    def breadth(self) -> int:
        return _SCOPE_BREADTH[self]


# BUILD_GRAPH (changed modules plus their dependencies) is broader than
# MODULE and narrower than the whole repository; it is ranked with MODULE's
# successor so "never narrower than the provider minimum" stays a total order.
_SCOPE_BREADTH = {
    ScanScope.CHANGED_FILES: 0, ScanScope.MODULE: 1,
    ScanScope.BUILD_GRAPH: 2, ScanScope.REPOSITORY: 3,
}


class ChangeKind(str, Enum):
    ADDED = "added"          # POST only
    MODIFIED = "modified"    # PRE old bytes, POST candidate bytes
    DELETED = "deleted"      # PRE only


class Side(str, Enum):
    PRE = "pre"
    POST = "post"


class ScanStatus(str, Enum):
    COMPLETE = "COMPLETE"
    # Parsed, but at least one intended target is not scanner-confirmed.
    INCOMPLETE = "INCOMPLETE"
    # A rule or configuration error reported by the scanner.
    CONFIG_ERROR = "CONFIG_ERROR"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"


class TargetStatus(str, Enum):
    COVERED = "covered"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    NO_RULES = "no_rules"
    PREREQUISITE_MISSING = "prerequisite_missing"
    # Supported, with rules, but the scanner did not confirm analyzing it.
    NOT_ANALYZED = "not_analyzed"
    # Above the configured size limit; never submitted (Kriya-enforced).
    OVERSIZED = "oversized"


class CoverageStatus(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    PREREQUISITES_MISSING = "PREREQUISITES_MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Outcome(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    # A finding that would block, released by a valid operator waiver.
    # Never PASS.
    ACCEPTED_RISK = "ACCEPTED_RISK"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


# The terminal_gate_outcome event status per outcome. DISABLED is never
# "passed".
GATE_EVENT_STATUS = {
    Outcome.PASS: "passed",
    Outcome.PASS_WITH_WARNINGS: "passed_with_warnings",
    Outcome.ACCEPTED_RISK: "accepted_risk",
    Outcome.BLOCKED: "failed",
    Outcome.UNKNOWN: "unknown",
    Outcome.UNAVAILABLE: "unavailable",
    Outcome.DISABLED: "disabled",
}

ACCEPTED_RISK_BANNER = "ACCEPTED RISK — NOT A CLEAN PASS"

# Closed reason-code table (tests/test_prd031a_static_analysis.py tripwire).
NEW_FINDING_BLOCKED = "NEW_FINDING_BLOCKED"
WORSENED_FINDING_BLOCKED = "WORSENED_FINDING_BLOCKED"
EXISTING_FINDING_BLOCKED = "EXISTING_FINDING_BLOCKED"
FINDING_WARNED = "FINDING_WARNED"
COVERAGE_PARTIAL = "COVERAGE_PARTIAL"
COVERAGE_UNSUPPORTED = "COVERAGE_UNSUPPORTED"
PREREQUISITES_MISSING = "PREREQUISITES_MISSING"
TARGET_OVERSIZED = "TARGET_OVERSIZED"
PROVIDER_NOT_REGISTERED = "PROVIDER_NOT_REGISTERED"
PROVIDER_PROBE_FAILED = "PROVIDER_PROBE_FAILED"
PROVIDER_VERSION_MISMATCH = "PROVIDER_VERSION_MISMATCH"
CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
EGRESS_NOT_PERMITTED = "EGRESS_NOT_PERMITTED"
CONTAINMENT_UNAVAILABLE = "CONTAINMENT_UNAVAILABLE"
SCOPE_BELOW_PROVIDER_MINIMUM = "SCOPE_BELOW_PROVIDER_MINIMUM"
SCOPE_UNRESOLVABLE = "SCOPE_UNRESOLVABLE"
SCAN_TIMEOUT = "SCAN_TIMEOUT"
SCAN_FAILED = "SCAN_FAILED"
SCAN_OUTPUT_MALFORMED = "SCAN_OUTPUT_MALFORMED"
SCAN_INCOMPLETE = "SCAN_INCOMPLETE"
RULE_PACK_INVALID = "RULE_PACK_INVALID"
RULE_PACK_UNPINNED = "RULE_PACK_UNPINNED"
RULE_PACK_DIGEST_MISMATCH = "RULE_PACK_DIGEST_MISMATCH"
BASELINE_NOT_COMPARABLE = "BASELINE_NOT_COMPARABLE"
BASELINE_IDENTITY_MISMATCH = "BASELINE_IDENTITY_MISMATCH"
BASELINE_UNAVAILABLE_IN_PLACE = "BASELINE_UNAVAILABLE_IN_PLACE"
WAIVER_APPLIED = "WAIVER_APPLIED"
WAIVER_EXPIRED = "WAIVER_EXPIRED"
WAIVER_SCOPE_MISMATCH = "WAIVER_SCOPE_MISMATCH"
WAIVER_SEVERITY_EXCEEDED = "WAIVER_SEVERITY_EXCEEDED"
WAIVER_RULE_PACK_MISMATCH = "WAIVER_RULE_PACK_MISMATCH"
WAIVER_DIGEST_INVALID = "WAIVER_DIGEST_INVALID"
WAIVER_WORKSPACE_MISMATCH = "WAIVER_WORKSPACE_MISMATCH"
WAIVER_STORE_INVALID = "WAIVER_STORE_INVALID"
STATIC_ANALYSIS_DISABLED = "STATIC_ANALYSIS_DISABLED"
STATIC_ANALYSIS_NOT_CONFIGURED = "STATIC_ANALYSIS_NOT_CONFIGURED"
STATIC_ANALYSIS_EVIDENCE_STALE = "STATIC_ANALYSIS_EVIDENCE_STALE"
STATIC_ANALYSIS_EVIDENCE_MISSING = "STATIC_ANALYSIS_EVIDENCE_MISSING"
STATIC_ANALYSIS_NOT_PERMITTED = "STATIC_ANALYSIS_NOT_PERMITTED"
STATIC_ANALYSIS_INTERNAL_ERROR = "STATIC_ANALYSIS_INTERNAL_ERROR"

REASON_CODES = frozenset({
    NEW_FINDING_BLOCKED, WORSENED_FINDING_BLOCKED, EXISTING_FINDING_BLOCKED, FINDING_WARNED,
    COVERAGE_PARTIAL, COVERAGE_UNSUPPORTED, PREREQUISITES_MISSING, TARGET_OVERSIZED,
    PROVIDER_NOT_REGISTERED, PROVIDER_PROBE_FAILED, PROVIDER_VERSION_MISMATCH, CAPABILITY_UNKNOWN,
    EGRESS_NOT_PERMITTED, CONTAINMENT_UNAVAILABLE, SCOPE_BELOW_PROVIDER_MINIMUM, SCOPE_UNRESOLVABLE,
    SCAN_TIMEOUT, SCAN_FAILED, SCAN_OUTPUT_MALFORMED, SCAN_INCOMPLETE,
    RULE_PACK_INVALID, RULE_PACK_UNPINNED, RULE_PACK_DIGEST_MISMATCH,
    BASELINE_NOT_COMPARABLE, BASELINE_IDENTITY_MISMATCH, BASELINE_UNAVAILABLE_IN_PLACE,
    WAIVER_APPLIED, WAIVER_EXPIRED, WAIVER_SCOPE_MISMATCH, WAIVER_SEVERITY_EXCEEDED,
    WAIVER_RULE_PACK_MISMATCH, WAIVER_DIGEST_INVALID, WAIVER_WORKSPACE_MISMATCH, WAIVER_STORE_INVALID,
    STATIC_ANALYSIS_DISABLED, STATIC_ANALYSIS_NOT_CONFIGURED, STATIC_ANALYSIS_EVIDENCE_STALE,
    STATIC_ANALYSIS_EVIDENCE_MISSING, STATIC_ANALYSIS_NOT_PERMITTED, STATIC_ANALYSIS_INTERNAL_ERROR,
})


# --- Provider identity and capability ------------------------------------

@dataclass(frozen=True)
class RulePackIdentity:
    ref: str
    digest: str
    rule_count: int
    languages: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"ref": self.ref, "digest": self.digest, "rule_count": self.rule_count,
                "languages": list(self.languages)}


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    version: str
    edition: str
    # sha256 of the resolved executable, or the pinned image digest.
    executable_digest: str
    execution_location: str          # "local_process" | "container"
    network_enforced: bool
    rule_packs: Tuple[RulePackIdentity, ...]
    # The exact adapter-owned flag set and environment (argv minus targets).
    effective_options_digest: str
    severity_map_version: int

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider, "version": self.version, "edition": self.edition,
            "executable_digest": self.executable_digest, "execution_location": self.execution_location,
            "network_enforced": self.network_enforced,
            "rule_packs": [pack.to_dict() for pack in self.rule_packs],
            "effective_options_digest": self.effective_options_digest,
            "severity_map_version": self.severity_map_version,
        }


@dataclass(frozen=True)
class LanguageSupport:
    maturity: str                    # "ga" | "beta" | "experimental"
    rules_available: int


MATURITY_RANK = {"experimental": 0, "beta": 1, "ga": 2}


@dataclass(frozen=True)
class ProviderCapability:
    languages: Mapping[str, LanguageSupport]
    analysis_scope: str              # "file_local" | "cross_file"
    supported_scopes: FrozenSet[ScanScope]
    minimum_scope: ScanScope
    network_requirement: str         # "none" | "rule_download" | "service"
    source_upload: bool
    # language -> prerequisite ids the provider needs before analysis.
    prerequisites: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    # Scanner-control file names never copied into a snapshot.
    control_files: Tuple[str, ...] = ()
    # Known limitations the evidence must disclose (never claimed away).
    limitations: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "languages": {lang: {"maturity": s.maturity, "rules_available": s.rules_available}
                          for lang, s in sorted(self.languages.items())},
            "analysis_scope": self.analysis_scope,
            "supported_scopes": sorted(s.value for s in self.supported_scopes),
            "minimum_scope": self.minimum_scope.value,
            "network_requirement": self.network_requirement, "source_upload": self.source_upload,
            "prerequisites": {k: list(v) for k, v in sorted(self.prerequisites.items())},
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ProviderProbe:
    """Either an identity and capability, or the reason there is none."""

    identity: Optional[ProviderIdentity]
    capability: Optional[ProviderCapability]
    reason_code: Optional[str] = None
    detail: str = ""


@dataclass(frozen=True)
class PrerequisiteResult:
    language: str
    prerequisite: str
    met: bool
    detail: str = ""


# --- Change set, scope plan and snapshots ---------------------------------

@dataclass(frozen=True)
class ChangedPath:
    relpath: str
    kind: ChangeKind
    # POST bytes (None for DELETED).
    post_bytes: Optional[bytes]
    expected_base_revision: str


@dataclass(frozen=True)
class OversizedTarget:
    relpath: str
    side: Side
    size: int
    limit: int
    reason: str = "exceeds static_analysis.max_target_bytes"

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.relpath, "side": self.side.value, "size": self.size,
                "limit": self.limit, "reason": self.reason}


@dataclass(frozen=True)
class ScopePlan:
    kind: ScanScope
    provider_minimum: ScanScope
    # Scope members as they exist on each side (relpath -> sha256), before
    # size filtering: the identity the commit re-checks.
    pre_members: Mapping[str, str]
    post_members: Mapping[str, str]
    # What is actually submitted to the provider on each side: existing,
    # within the size limit, not excluded.
    pre_targets: Tuple[str, ...]
    post_targets: Tuple[str, ...]
    changed: Tuple[ChangedPath, ...]
    module_roots: Tuple[str, ...]
    exclusions: Tuple[str, ...]
    excluded: Tuple[str, ...]
    oversized: Tuple[OversizedTarget, ...]
    max_target_bytes: int

    @property
    def base_scope_digest(self) -> str:
        return canonical_digest(sorted(self.pre_members.items()))

    @property
    def post_scope_digest(self) -> str:
        return canonical_digest(sorted(self.post_members.items()))

    @property
    def plan_digest(self) -> str:
        return canonical_digest({
            "kind": self.kind.value, "provider_minimum": self.provider_minimum.value,
            "pre_members": sorted(self.pre_members), "post_members": sorted(self.post_members),
            "module_roots": list(self.module_roots), "exclusions": list(self.exclusions),
            "excluded": list(self.excluded), "max_target_bytes": self.max_target_bytes,
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value, "provider_minimum": self.provider_minimum.value,
            "pre_targets": len(self.pre_targets), "post_targets": len(self.post_targets),
            "changed": [{"path": c.relpath, "kind": c.kind.value} for c in self.changed],
            "module_roots": list(self.module_roots), "exclusions": list(self.exclusions),
            "excluded": list(self.excluded), "oversized": [o.to_dict() for o in self.oversized],
            "max_target_bytes": self.max_target_bytes, "plan_digest": self.plan_digest,
            "base_scope_digest": self.base_scope_digest, "post_scope_digest": self.post_scope_digest,
        }


# --- Scan results and findings ---------------------------------------------

@dataclass(frozen=True)
class Finding:
    """One provider finding, normalized by the adapter."""

    provider: str
    rule_id: str                     # "<provider>:<provider rule id>"
    severity: Severity
    path: str                        # snapshot-relative == workspace-relative
    start_line: int
    end_line: int
    start_col: int = 0
    end_col: int = 0
    category: Optional[str] = None
    cwe: Tuple[str, ...] = ()
    owasp: Tuple[str, ...] = ()
    # Provider text: untrusted, bounded, never a policy input.
    message: str = ""
    raw_index: int = -1


@dataclass(frozen=True)
class ScanError:
    kind: str                        # "config" | "target" | "other"
    message: str
    path: Optional[str] = None
    level: str = "error"


@dataclass(frozen=True)
class ScanResult:
    status: ScanStatus
    findings: Tuple[Finding, ...] = ()
    # Scanner-confirmed analyzed paths: scanned - skipped - errored.
    analyzed: FrozenSet[str] = frozenset()
    skipped: Mapping[str, str] = field(default_factory=dict)
    errors: Tuple[ScanError, ...] = ()
    raw_sha256: Optional[str] = None
    raw_output: Optional[str] = None
    duration_ms: int = 0
    reason_code: Optional[str] = None
    detail: str = ""
    # The tool version the scan's own output reports (None if it reports
    # none): PRE and POST are comparable only under one identity.
    reported_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value, "findings": len(self.findings),
            "reported_version": self.reported_version,
            "analyzed": len(self.analyzed), "skipped": dict(sorted(self.skipped.items())),
            "errors": [{"kind": e.kind, "path": e.path, "level": e.level} for e in self.errors],
            "raw_sha256": self.raw_sha256, "duration_ms": self.duration_ms,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class ClassifiedFinding:
    finding: Finding
    fingerprint: str
    location_key: str
    classification: Classification
    side: Side                       # the scan the reported finding comes from

    @property
    def pre_state(self) -> str:
        return "absent" if self.classification is Classification.INTRODUCED else "present"

    @property
    def post_state(self) -> str:
        return "absent" if self.classification is Classification.RESOLVED else "present"


@dataclass(frozen=True)
class TargetCoverage:
    relpath: str
    language: Optional[str]
    status: TargetStatus
    sides: Tuple[Side, ...]
    detail: str = ""


@dataclass(frozen=True)
class CoverageReport:
    status: CoverageStatus
    targets: Tuple[TargetCoverage, ...]
    unclassified: Tuple[str, ...]

    @property
    def uncovered(self) -> Tuple[TargetCoverage, ...]:
        return tuple(t for t in self.targets if t.status is not TargetStatus.COVERED)

    def to_dict(self) -> Dict[str, Any]:
        per_language: Dict[str, Dict[str, int]] = {}
        for target in self.targets:
            row = per_language.setdefault(target.language or "unclassified", {"files": 0, "covered": 0})
            row["files"] += 1
            row["covered"] += target.status is TargetStatus.COVERED
        return {
            "status": self.status.value,
            "per_language": [{"language": k, **v} for k, v in sorted(per_language.items())],
            "confirmed_analyzed": sum(t.status is TargetStatus.COVERED for t in self.targets),
            "uncovered": [{"path": t.relpath, "status": t.status.value, "detail": t.detail}
                          for t in self.uncovered],
            "unclassified": list(self.unclassified),
        }

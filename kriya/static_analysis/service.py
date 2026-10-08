"""StaticAnalysisService and the commit guard (PRD-031A §4, §10).

``StaticAnalysisService.evaluate`` is the one static-analysis gate. Both
terminal commit boundaries call it with the exact batch they are about to
commit: the enforce TerminalGateService and the direct/milestone pre-apply
boundary. It never writes the real workspace: it reads it (the pristine
base until commit), builds Kriya-owned snapshots in a temp directory, and
writes evidence under the state directory.

``commit_guard`` builds the only object ``commit_terminal_candidate``
accepts. At commit it refuses evidence that is missing or not permitting,
and re-checks every identity component the verdict depends on - the batch,
the base of the scope, the scope plan, the provider runtime and rule packs,
and the effective settings - so nothing that changed after the scan can be
committed on its evidence.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.static_analysis import policy as policy_module
from kriya.static_analysis.baseline import diff_findings, summary
from kriya.static_analysis.coverage import confirm_coverage, evaluate_coverage, language_of
from kriya.static_analysis.model import (
    ACCEPTED_RISK_BANNER,
    BASELINE_IDENTITY_MISMATCH,
    BASELINE_NOT_COMPARABLE,
    BASELINE_UNAVAILABLE_IN_PLACE,
    CONTAINMENT_UNAVAILABLE,
    EGRESS_NOT_PERMITTED,
    GATE_EVENT_STATUS,
    PROVIDER_NOT_REGISTERED,
    PROVIDER_PROBE_FAILED,
    SCAN_FAILED,
    SCAN_OUTPUT_MALFORMED,
    SCAN_TIMEOUT,
    SCOPE_BELOW_PROVIDER_MINIMUM,
    SCOPE_UNRESOLVABLE,
    STATIC_ANALYSIS_DISABLED,
    STATIC_ANALYSIS_EVIDENCE_MISSING,
    STATIC_ANALYSIS_EVIDENCE_STALE,
    STATIC_ANALYSIS_INTERNAL_ERROR,
    STATIC_ANALYSIS_NOT_CONFIGURED,
    STATIC_ANALYSIS_NOT_PERMITTED,
    WAIVER_STORE_INVALID,
    Outcome,
    ProviderCapability,
    ProviderIdentity,
    ScanResult,
    ScanScope,
    ScanStatus,
    Side,
    TargetStatus,
    bytes_digest,
    canonical_digest,
)
from kriya.static_analysis.port import ExecutionContext, ScanRequest, StaticAnalysisPort
from kriya.static_analysis.registry import ProviderNotRegisteredError, create_provider
from kriya.static_analysis.scope import (
    ScopeError,
    ScopeInputs,
    build_change_set,
    build_scope,
    resolve_roots,
    workspace_relpath,
)
from kriya.static_analysis.waivers import (
    WaiverRejection,
    WaiverStore,
    load_waivers,
    match_waivers,
    waiver_store_path,
)
from kriya.tools.containment import ContainmentSetupError, resolve_containment_backend

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION = 1
# Bounded counts in a result/event (the full list is in the evidence file).
_REPORTED_FINDINGS = 50
_SCAN_FAILURE_REASON = {
    ScanStatus.FAILED: SCAN_FAILED, ScanStatus.TIMEOUT: SCAN_TIMEOUT,
    ScanStatus.MALFORMED_OUTPUT: SCAN_OUTPUT_MALFORMED,
}


def analysis_enabled(cfg: Any) -> bool:
    """Exactly ``static_analysis.enabled is True`` on a real configuration.
    A configuration without the section (a config-less engine) has no
    static analysis; a truthy non-bool (a test double) never enables it."""
    static = getattr(cfg, "static_analysis", None)
    return static is not None and getattr(static, "enabled", False) is True


def batch_digest(writes: Sequence[Any], workspace_path: str) -> str:
    """The candidate (paths, bytes, modes, deletions) and the base each
    changed path is grounded on. Paths compare as real paths."""
    entries = []
    for write in writes:
        data = write.content_bytes if write.content_bytes is not None else write.content.encode("utf-8")
        entries.append([
            workspace_relpath(write.target_path, workspace_path), write.delete,
            None if write.delete else bytes_digest(data), write.mode,
            write.expected_base_revision, write.expected_base_exists,
        ])
    return canonical_digest(sorted(entries, key=lambda entry: entry[0]))


def settings_digest(cfg: Any) -> str:
    """Everything configurable that can change a verdict: the static-analysis
    section and the execution authority it runs under."""
    autonomy = cfg.autonomy
    return canonical_digest({
        "static_analysis": cfg.static_analysis.model_dump(mode="json"),
        "contained_execution_required": autonomy.contained_execution_required,
        "containment_backend": autonomy.containment_backend,
        "egress_policy": autonomy.egress_policy,
        "runtime_profile": cfg.runtime_profile,
    })


@dataclass(frozen=True)
class EvidenceIdentity:
    """Every component the verdict depends on; the commit guard recomputes each."""

    batch_digest: str
    base_scope_digest: str
    post_scope_digest: str
    plan_digest: str
    scope_inputs: ScopeInputs
    provider: str
    provider_identity_digest: str
    runtime_fingerprint: str
    settings_digest: str

    @property
    def authorization_digest(self) -> str:
        return canonical_digest([
            self.batch_digest, self.base_scope_digest, self.post_scope_digest, self.plan_digest,
            self.provider, self.provider_identity_digest, self.runtime_fingerprint, self.settings_digest,
        ])


@dataclass(frozen=True)
class StaticAnalysisRequest:
    # The exact StagedFileWrite batch the commit will receive.
    writes: Sequence[Any]
    workspace_path: str
    run_id: str
    unit_id: str
    # The candidate was generated in the real workspace: no pristine base on disk. STATIC-ANALYSIS-INPLACE-
    # BASELINE-001: ``originals`` (relpath -> the bytes captured before the write, None when it did not exist)
    # supply the PRE side instead; without them an in-place candidate is refused (BASELINE_UNAVAILABLE_IN_PLACE).
    in_place: bool = False
    originals: Optional[Mapping[str, Optional[bytes]]] = None
    # The workspace whose identity keys the waiver store; defaults to
    # workspace_path (the operator scan evaluates a base worktree of it).
    identity_root: Optional[str] = None


@dataclass(frozen=True)
class StaticAnalysisCandidate:
    """A candidate whose batch is materialized only when analysis is
    enabled (the enforce boundary: the commit re-materializes the same
    batch, and the guard proves it is byte-identical)."""

    materialize: Callable[[], Sequence[Any]]
    workspace_path: str
    run_id: str
    unit_id: str
    in_place: bool = False
    originals: Optional[Mapping[str, Optional[bytes]]] = None  # in-place: the captured pre-write bytes


@dataclass(frozen=True)
class StaticAnalysisGateResult:
    outcome: Outcome
    requirement: str
    permits_commit: bool
    reason_codes: Tuple[str, ...]
    evidence: Mapping[str, Any]
    identity: Optional[EvidenceIdentity] = None
    waiver_ids: Tuple[str, ...] = ()
    # In-memory only (never persisted): the in-place candidate's captured originals, so the commit guard re-derives
    # the same PRE side the scan used instead of reading candidate bytes from disk as the base.
    originals: Optional[Mapping[str, Optional[bytes]]] = field(default=None, compare=False, repr=False)

    @property
    def accepted_risk(self) -> bool:
        return self.outcome is Outcome.ACCEPTED_RISK

    @property
    def gate_status(self) -> str:
        return GATE_EVENT_STATUS[self.outcome]

    @property
    def evidence_digest(self) -> str:
        return str(self.evidence.get("evidence_digest", ""))

    @property
    def gap(self) -> Optional[str]:
        """The blocking message (None when the commit is permitted). Built
        only from outcome, reason codes, counts, rule ids, paths,
        fingerprints and waiver ids - never scanner text."""
        if self.permits_commit:
            return None
        return f"STATIC_ANALYSIS_{self.outcome.value}: {describe(self)}"

    def summary(self) -> Dict[str, Any]:
        """The bounded result/event payload."""
        evidence = self.evidence
        return {
            "outcome": self.outcome.value, "requirement": self.requirement,
            "permits_commit": self.permits_commit, "accepted_risk": self.accepted_risk,
            "reason_codes": list(self.reason_codes), "waiver_ids": list(self.waiver_ids),
            "banner": banner(self),
            "summary": evidence.get("summary"), "coverage": (evidence.get("coverage") or {}).get("status"),
            "provider": (evidence.get("provider") or {}).get("provider"),
            "evidence_digest": self.evidence_digest, "evidence_path": evidence.get("evidence_path"),
        }


def static_analysis_result_fields(result: Optional[StaticAnalysisGateResult]) -> Dict[str, Any]:
    """The run result's static-analysis keys. Absent result (the gate did
    not run) reports None - readers treat that as not evaluated, never PASS."""
    return {
        "static_analysis": result.summary() if result is not None else None,
        "accepted_risk": bool(result is not None and result.accepted_risk),
        "accepted_risks": list(result.waiver_ids) if result is not None and result.accepted_risk else [],
    }


def describe(result: StaticAnalysisGateResult) -> str:
    parts = [f"reason codes {', '.join(result.reason_codes) or 'none'}"]
    counts = result.evidence.get("summary")
    if counts:
        parts.append(", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    blocked = [
        f"{f['rule_id']} at {f['path']} ({f['fingerprint'][:12]})"
        for f in result.evidence.get("findings", []) if f.get("decision") == policy_module.BLOCK
    ]
    if blocked:
        parts.append("blocking: " + "; ".join(blocked[:10]) + (" ..." if len(blocked) > 10 else ""))
    if result.waiver_ids:
        parts.append("waivers: " + ", ".join(result.waiver_ids))
    return "; ".join(parts)


def banner(result: StaticAnalysisGateResult) -> str:
    """The unmissable human line. ACCEPTED_RISK is never presented as PASS."""
    if result.outcome is Outcome.ACCEPTED_RISK:
        return f"{ACCEPTED_RISK_BANNER} (waivers: {', '.join(result.waiver_ids)})"
    if result.outcome is Outcome.PASS:
        return "STATIC ANALYSIS: PASS"
    return f"STATIC ANALYSIS: {result.outcome.value.replace('_', ' ')} ({', '.join(result.reason_codes) or 'no reason codes'})"


def _finish(
    outcome: Outcome, reason_codes: Sequence[str], evidence: Dict[str, Any], *, requirement: str,
    when_unavailable: str, identity: Optional[EvidenceIdentity] = None, waiver_ids: Sequence[str] = (),
) -> StaticAnalysisGateResult:
    permits = policy_module.permits_commit(outcome, requirement=requirement, when_unavailable=when_unavailable)
    evidence = {
        **evidence, "schema_version": EVIDENCE_SCHEMA_VERSION, "outcome": outcome.value,
        "requirement": requirement, "permits_commit": permits,
        "accepted_risk": outcome is Outcome.ACCEPTED_RISK,
        "reason_codes": sorted(set(reason_codes)),
        "authorization_digest": identity.authorization_digest if identity is not None else None,
    }
    # Raw provider output is stored beside the evidence (sha256-referenced
    # in scans.*.raw_sha256), not inside the digested document.
    evidence["evidence_digest"] = canonical_digest(
        {k: v for k, v in evidence.items() if k not in ("evidence_path", "_raw")}
    )
    return StaticAnalysisGateResult(
        outcome=outcome, requirement=requirement, permits_commit=permits,
        reason_codes=tuple(sorted(set(reason_codes))), evidence=evidence,
        identity=identity if permits else None, waiver_ids=tuple(waiver_ids),
    )


def disabled_result(static: Any) -> StaticAnalysisGateResult:
    """DISABLED, never PASS. ``static`` is None for a configuration without
    the section at all (a config-less engine): not configured."""
    configured = static is not None and "enabled" in getattr(static, "model_fields_set", ())
    reason = STATIC_ANALYSIS_DISABLED if configured else STATIC_ANALYSIS_NOT_CONFIGURED
    return _finish(
        Outcome.DISABLED, [reason], {"configured_by": "operator" if configured else "default"},
        requirement=getattr(static, "requirement", "optional"), when_unavailable="warn",
    )


def execution_context(cfg: Any, workspace_path: str) -> ExecutionContext:
    autonomy = cfg.autonomy
    backend = None
    if autonomy.contained_execution_required:
        backend = resolve_containment_backend(autonomy.containment_backend)
    return ExecutionContext(
        contained=autonomy.contained_execution_required, containment_backend=backend,
        workspace_root=os.path.realpath(workspace_path),
    )


ProviderCreator = Callable[[str, Dict[str, Any], ExecutionContext], StaticAnalysisPort]


class StaticAnalysisService:
    """The one static-analysis gate (both terminal boundaries)."""

    def __init__(
        self, cfg: Any, *, create: ProviderCreator = create_provider,
        clock: Callable[[], Optional[datetime]] = lambda: None,
        evidence_root: Optional[str] = None,
    ) -> None:
        self._cfg = cfg
        self._create = create
        self._clock = clock
        self._evidence_root = evidence_root

    def evaluate(self, request: StaticAnalysisRequest) -> StaticAnalysisGateResult:
        if not analysis_enabled(self._cfg):
            return disabled_result(getattr(self._cfg, "static_analysis", None))
        try:
            result = self._evaluate(request)
        except Exception:
            # Fail closed: an error inside the gate can never become PASS.
            logger.exception("static analysis gate failed internally")
            result = self._fail(Outcome.UNKNOWN, STATIC_ANALYSIS_INTERNAL_ERROR, {})
        return self._persist(request, result)

    def evaluate_candidate(self, candidate: StaticAnalysisCandidate) -> StaticAnalysisGateResult:
        if not analysis_enabled(self._cfg):
            return disabled_result(getattr(self._cfg, "static_analysis", None))
        request = StaticAnalysisRequest(
            writes=(), workspace_path=candidate.workspace_path, run_id=candidate.run_id,
            unit_id=candidate.unit_id, in_place=candidate.in_place,
        )
        if candidate.in_place and candidate.originals is None:
            return self.evaluate(request)
        # Imported here: terminal_commit imports this module (its guard type).
        from kriya.workflow.terminal_commit import CandidateMaterializationError

        try:
            writes = candidate.materialize()
        except CandidateMaterializationError as error:
            return self._persist(request, self._fail(
                Outcome.UNKNOWN, STATIC_ANALYSIS_INTERNAL_ERROR, {}, f"candidate not materializable: {error}",
            ))
        return self.evaluate(StaticAnalysisRequest(
            writes=writes, workspace_path=candidate.workspace_path, run_id=candidate.run_id,
            unit_id=candidate.unit_id, in_place=candidate.in_place, originals=candidate.originals,
        ))

    # -- helpers -------------------------------------------------------------

    def _fail(self, outcome: Outcome, reason: str, evidence: Dict[str, Any], detail: str = "") -> StaticAnalysisGateResult:
        static = self._cfg.static_analysis
        if detail:
            evidence = {**evidence, "detail": detail}
        return _finish(
            outcome, [reason], {"configured_by": "operator", **evidence},
            requirement=static.requirement, when_unavailable=static.policy.when_unavailable,
        )

    def _persist(self, request: StaticAnalysisRequest, result: StaticAnalysisGateResult) -> StaticAnalysisGateResult:
        """Write the evidence (and raw provider output) under the state
        directory. A write failure is recorded in the evidence, never fatal:
        the evidence digest and the result stay authoritative in memory."""
        raw = result.evidence.get("_raw", {})
        evidence = {k: v for k, v in result.evidence.items() if k != "_raw"}
        root = self._evidence_root or _default_evidence_root(self._cfg)
        directory = os.path.join(root, _safe_segment(request.run_id), _safe_segment(request.unit_id))
        try:
            os.makedirs(directory, exist_ok=True)
            for scan_id, text in raw.items():
                with open(os.path.join(directory, f"{scan_id}.raw.json"), "w", encoding="utf-8") as handle:
                    handle.write(text)
            path = os.path.join(directory, "evidence.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(evidence, handle, indent=2, sort_keys=True, default=str)
            evidence["evidence_path"] = path
        except OSError as error:
            logger.warning("static analysis evidence not persisted: %s", error)
            evidence["evidence_path"] = None
            evidence["evidence_persist_error"] = f"{type(error).__name__}: {error}"
        return StaticAnalysisGateResult(
            outcome=result.outcome, requirement=result.requirement, permits_commit=result.permits_commit,
            reason_codes=result.reason_codes, evidence=evidence, identity=result.identity,
            waiver_ids=result.waiver_ids, originals=request.originals,
        )

    # -- the gate ------------------------------------------------------------

    def _evaluate(self, request: StaticAnalysisRequest) -> StaticAnalysisGateResult:
        cfg = self._cfg
        static = cfg.static_analysis
        workspace = os.path.realpath(request.workspace_path)
        if request.in_place and request.originals is None:
            return self._fail(
                Outcome.UNKNOWN, BASELINE_UNAVAILABLE_IN_PLACE, {},
                "the candidate was generated in the real workspace and no pre-write originals were captured, so no "
                "PRE base exists",
            )
        settings = dict(static.providers.get(static.provider, {}))
        try:
            # The real workspace decides which rule packs must be pinned.
            context = execution_context(cfg, request.identity_root or workspace)
        except ContainmentSetupError as error:
            # Containment is required and no backend can provide it: never a
            # host fallback.
            return self._fail(Outcome.UNAVAILABLE, CONTAINMENT_UNAVAILABLE, {}, str(error))
        try:
            port = self._create(static.provider, settings, context)
        except ProviderNotRegisteredError as error:
            return self._fail(Outcome.UNAVAILABLE, PROVIDER_NOT_REGISTERED, {}, str(error))
        probe = port.probe()
        if probe.identity is None or probe.capability is None:
            return self._fail(
                Outcome.UNAVAILABLE, probe.reason_code or PROVIDER_PROBE_FAILED, {}, probe.detail,
            )
        identity, capability = probe.identity, probe.capability
        base_evidence: Dict[str, Any] = {
            "configured_by": "operator", "config_digest": settings_digest(cfg),
            "provider": {**identity.to_dict(), "identity_digest": identity.identity_digest},
            "capability": capability.to_dict(),
        }

        egress = egress_admission(capability, cfg.autonomy.egress_policy)
        base_evidence["egress"] = egress
        if not egress["admitted"]:
            return self._fail(Outcome.UNAVAILABLE, EGRESS_NOT_PERMITTED, base_evidence, egress["detail"])

        kind = capability.minimum_scope if static.scope == "auto" else ScanScope(static.scope)
        if kind.breadth < capability.minimum_scope.breadth:
            return self._fail(
                Outcome.UNAVAILABLE, SCOPE_BELOW_PROVIDER_MINIMUM, base_evidence,
                f"configured scope {kind.value} is narrower than the provider minimum {capability.minimum_scope.value}",
            )
        if kind not in capability.supported_scopes:
            return self._fail(
                Outcome.UNAVAILABLE, SCOPE_UNRESOLVABLE, base_evidence,
                f"the provider does not support scope {kind.value}",
            )

        try:
            changes, pre_bytes = build_change_set(request.writes, workspace, originals=request.originals)
            graph_roots = (
                port.build_graph_roots(workspace, [c.relpath for c in changes])
                if kind is ScanScope.BUILD_GRAPH else ()
            )
            inputs = ScopeInputs(
                kind=kind, provider_minimum=capability.minimum_scope,
                roots=resolve_roots(kind, workspace, changes, graph_roots),
                exclusions=tuple(static.exclusions), control_files=tuple(capability.control_files),
                max_target_bytes=static.max_target_bytes,
            )
        except ScopeError as error:
            outcome = Outcome.UNKNOWN if error.reason_code == BASELINE_IDENTITY_MISMATCH else Outcome.UNAVAILABLE
            return self._fail(outcome, error.reason_code, base_evidence, str(error))

        scratch = tempfile.mkdtemp(prefix="kriya-static-analysis-")
        try:
            return self._scan_and_decide(
                request, port, identity, capability, inputs, changes, pre_bytes, workspace, scratch, base_evidence,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _scan_and_decide(
        self, request: StaticAnalysisRequest, port: StaticAnalysisPort, identity: ProviderIdentity,
        capability: ProviderCapability, inputs: ScopeInputs, changes: Sequence[Any], pre_bytes: Mapping[str, bytes],
        workspace: str, scratch: str, base_evidence: Dict[str, Any],
    ) -> StaticAnalysisGateResult:
        static = self._cfg.static_analysis
        pre_dir, post_dir = os.path.join(scratch, "pre"), os.path.join(scratch, "post")
        os.makedirs(pre_dir)
        os.makedirs(post_dir)
        plan = build_scope(inputs, changes, pre_bytes, workspace, pre_dir=pre_dir, post_dir=post_dir)
        languages = sorted({language_of(p) for p in (*plan.pre_targets, *plan.post_targets)} - {None})
        prerequisites = port.check_prerequisites(capability, languages, workspace)
        min_maturity = str(static.providers.get(static.provider, {}).get("min_language_maturity", "ga"))
        coverage = evaluate_coverage(plan, capability, min_maturity=min_maturity, prerequisites=prerequisites)
        evidence: Dict[str, Any] = {
            **base_evidence, "scope": plan.to_dict(),
            "prerequisites": [p.__dict__ for p in prerequisites],
        }

        covered = {t.relpath for t in coverage.targets if t.status is TargetStatus.COVERED}
        scans: Dict[Side, Optional[ScanResult]] = {}
        raw: Dict[str, str] = {}
        for side, root, targets in (
            (Side.POST, post_dir, plan.post_targets), (Side.PRE, pre_dir, plan.pre_targets),
        ):
            submit = tuple(t for t in targets if t in covered)
            if not submit:
                scans[side] = None
                continue
            scan = port.scan(ScanRequest(
                root=root, targets=submit, timeout_seconds=static.timeout_seconds,
                scratch_dir=os.path.join(scratch, f"{side.value}-scratch"), scan_id=side.value,
                max_target_bytes=static.max_target_bytes,
            ))
            scans[side] = scan
            if scan.raw_output is not None:
                raw[side.value] = scan.raw_output
        evidence["scans"] = {side.value: (scan.to_dict() if scan is not None else None) for side, scan in scans.items()}
        evidence["_raw"] = raw

        failure = _scan_failure(scans)
        if failure is not None:
            outcome, reason = failure
            evidence["coverage"] = coverage.to_dict()
            return self._fail(outcome, reason, evidence)

        coverage = confirm_coverage(coverage, scans)
        evidence["coverage"] = coverage.to_dict()
        comparable = _comparable(scans, identity)
        evidence["scans"]["comparable"] = comparable
        pre_scan, post_scan = scans.get(Side.PRE), scans.get(Side.POST)
        classified = diff_findings(
            pre_dir, pre_scan.findings if pre_scan is not None else (),
            post_dir, post_scan.findings if post_scan is not None else (),
            comparable=comparable,
        )

        waiver_root = os.path.realpath(request.identity_root or workspace)
        store_state = self._load_store(waiver_root)
        rejections: List[WaiverRejection] = []
        rule_pack_digests = [pack.digest for pack in identity.rule_packs]

        def matcher(blocked: Sequence[Any]) -> Dict[str, Any]:
            applied, rejected = match_waivers(
                blocked, store_state, workspace_root=waiver_root, rule_pack_digests=rule_pack_digests,
                now=self._clock(),
            )
            rejections.extend(rejected)
            return applied

        verdict = policy_module.decide(classified, coverage, static.policy, matcher)
        reasons = list(verdict.reason_codes)
        outcome = verdict.outcome
        if not comparable:
            reasons.append(BASELINE_NOT_COMPARABLE)
            if outcome is not Outcome.PASS:
                outcome = Outcome.UNKNOWN
        if store_state.status == "invalid":
            reasons.append(WAIVER_STORE_INVALID)

        waiver_ids = tuple(sorted({d.waiver_id for d in verdict.decisions if d.waiver_id}))
        evidence.update({
            "findings": [d.to_dict() for d in verdict.decisions],
            "summary": {**summary(classified), **_decision_counts(verdict.decisions)},
            "waivers": {
                "store": store_state.path, "store_status": store_state.status, "store_error": store_state.error,
                "applied": [
                    {"waiver_id": d.waiver_id, "fingerprint": d.item.fingerprint}
                    for d in verdict.decisions if d.waiver_id
                ],
                "rejected": [r.to_dict() for r in rejections],
            },
        })
        identity_record = EvidenceIdentity(
            batch_digest=batch_digest(request.writes, workspace), base_scope_digest=plan.base_scope_digest,
            post_scope_digest=plan.post_scope_digest, plan_digest=plan.plan_digest, scope_inputs=inputs,
            provider=static.provider, provider_identity_digest=identity.identity_digest,
            runtime_fingerprint=port.runtime_fingerprint(), settings_digest=settings_digest(self._cfg),
        )
        evidence["candidate"] = {"batch_digest": identity_record.batch_digest, "paths": len(request.writes)}
        evidence["runtime_fingerprint"] = identity_record.runtime_fingerprint
        return _finish(
            outcome, reasons, evidence, requirement=static.requirement,
            when_unavailable=static.policy.when_unavailable, identity=identity_record, waiver_ids=waiver_ids,
        )

    def _load_store(self, workspace: str) -> WaiverStore:
        try:
            path = waiver_store_path(self._cfg.static_analysis.waivers.store, workspace)
        except ValueError as error:
            return WaiverStore(path=str(self._cfg.static_analysis.waivers.store), status="invalid", error=str(error))
        return load_waivers(path, workspace)


def egress_admission(capability: ProviderCapability, egress_policy: str) -> Dict[str, Any]:
    """PRD-012 egress: a provider needing the network is refused under
    local_only; a source-uploading provider is refused under every policy
    in v1. Decided before any execution."""
    requirement = capability.network_requirement
    if capability.source_upload:
        return {"policy": egress_policy, "provider_network_requirement": requirement, "source_upload": True,
                "admitted": False, "detail": "providers that upload source are not admitted (PRD-031A v1)"}
    if requirement != "none" and egress_policy == "local_only":
        return {"policy": egress_policy, "provider_network_requirement": requirement, "source_upload": False,
                "admitted": False, "detail": f"network requirement {requirement!r} under egress_policy local_only"}
    return {"policy": egress_policy, "provider_network_requirement": requirement, "source_upload": False,
            "admitted": True, "detail": ""}


def _scan_failure(scans: Mapping[Side, Optional[ScanResult]]) -> Optional[Tuple[Outcome, str]]:
    """A scanner or configuration failure on either side (never PASS):
    configuration errors make the provider UNAVAILABLE, anything else is
    UNKNOWN evidence."""
    worst: Optional[Tuple[Outcome, str]] = None
    for scan in scans.values():
        if scan is None:
            continue
        if scan.status in _SCAN_FAILURE_REASON:
            return Outcome.UNKNOWN, scan.reason_code or _SCAN_FAILURE_REASON[scan.status]
        if scan.status is ScanStatus.CONFIG_ERROR and worst is None:
            worst = (Outcome.UNAVAILABLE, scan.reason_code or SCAN_FAILED)
    return worst


def _comparable(scans: Mapping[Side, Optional[ScanResult]], identity: ProviderIdentity) -> bool:
    """PRE and POST are comparable only under one identity: every version a
    scan reports must equal the probed one."""
    return all(
        scan.reported_version in (None, identity.version) for scan in scans.values() if scan is not None
    )


def _decision_counts(decisions: Sequence[Any]) -> Dict[str, int]:
    counts = {"blocked": 0, "warned": 0, "accepted": 0}
    names = {policy_module.BLOCK: "blocked", policy_module.WARN: "warned", policy_module.ACCEPTED: "accepted"}
    for decision in decisions:
        if decision.decision in names:
            counts[names[decision.decision]] += 1
    return counts


def _default_evidence_root(cfg: Any) -> str:
    from kriya.core.state_paths import resolve_state_directory

    return os.path.join(resolve_state_directory(cfg)[0], "static_analysis")


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value).strip(".")
    return cleaned[:120] or "unit"


# --- The commit guard ---------------------------------------------------------

@dataclass(frozen=True)
class CommitRefusal:
    reason_code: str
    detail: str


@dataclass(frozen=True)
class StaticAnalysisCommitGuard:
    """Built only by ``commit_guard``; the required argument of
    ``commit_terminal_candidate``."""

    cfg: Any
    result: Optional[StaticAnalysisGateResult]
    create: ProviderCreator = field(default=create_provider, compare=False)

    def evidence_id(self) -> Optional[str]:
        if self.result is None:
            return None
        return f"static_analysis:{self.result.outcome.value}:{self.result.evidence_digest}"

    def verify(self, writes: Sequence[Any], workspace_path: str) -> Optional[CommitRefusal]:
        result = self.result
        if not analysis_enabled(self.cfg):
            if result is not None and result.outcome is not Outcome.DISABLED:
                return CommitRefusal(STATIC_ANALYSIS_EVIDENCE_STALE, "static analysis was disabled after the scan")
            return None
        if result is None or result.outcome is Outcome.DISABLED:
            return CommitRefusal(STATIC_ANALYSIS_EVIDENCE_MISSING, "static analysis is enabled but no evidence was produced")
        static = self.cfg.static_analysis
        still_permitted = policy_module.permits_commit(
            result.outcome, requirement=static.requirement, when_unavailable=static.policy.when_unavailable,
        )
        if not (result.permits_commit and still_permitted):
            return CommitRefusal(STATIC_ANALYSIS_NOT_PERMITTED, f"static analysis outcome {result.outcome.value}")
        if result.outcome is Outcome.UNAVAILABLE:
            return None  # an optional, operator-accepted unavailable provider: nothing was scanned
        identity = result.identity
        if identity is None:
            return CommitRefusal(STATIC_ANALYSIS_EVIDENCE_MISSING, "the evidence carries no identity to verify")
        return _stale_component(self.cfg, identity, writes, os.path.realpath(workspace_path), self.create,
                                originals=result.originals)


def _stale_component(
    cfg: Any, identity: EvidenceIdentity, writes: Sequence[Any], workspace: str, create: ProviderCreator,
    originals: Optional[Mapping[str, Optional[bytes]]] = None,
) -> Optional[CommitRefusal]:
    def stale(component: str, detail: str = "") -> CommitRefusal:
        return CommitRefusal(STATIC_ANALYSIS_EVIDENCE_STALE, f"{component} changed after the scan{': ' + detail if detail else ''}")

    if settings_digest(cfg) != identity.settings_digest:
        return stale("effective settings")
    if batch_digest(writes, workspace) != identity.batch_digest:
        return stale("candidate batch")
    try:
        changes, pre_bytes = build_change_set(writes, workspace, originals=originals)
        plan = build_scope(identity.scope_inputs, changes, pre_bytes, workspace)
    except (ScopeError, OSError) as error:
        return stale("base", str(error))
    if plan.base_scope_digest != identity.base_scope_digest:
        return stale("base scope")
    if plan.plan_digest != identity.plan_digest:
        return stale("scope membership")
    if plan.post_scope_digest != identity.post_scope_digest:
        return stale("candidate scope")
    static = cfg.static_analysis
    try:
        port = create(static.provider, dict(static.providers.get(static.provider, {})), execution_context(cfg, workspace))
        fingerprint = port.runtime_fingerprint()
    except (ProviderNotRegisteredError, ContainmentSetupError, OSError, ValueError) as error:
        return stale("provider runtime", f"{type(error).__name__}: {error}")
    if fingerprint != identity.runtime_fingerprint:
        return stale("provider runtime or rule packs")
    return None


def commit_guard(cfg: Any, result: Optional[StaticAnalysisGateResult]) -> StaticAnalysisCommitGuard:
    """The only way to build the guard ``commit_terminal_candidate`` requires.
    Whether evidence is required is read from the current configuration,
    never chosen by the caller."""
    return StaticAnalysisCommitGuard(cfg=cfg, result=result)

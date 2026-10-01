"""Static-analysis readiness rows for ``kriya doctor --production`` (PRD-031A §14).

The same registry, probe, coverage, egress and waiver code the gate uses -
no second implementation. Each row is PASS, WARN, FAIL or NOT_APPLICABLE;
kriya/production_doctor.py renders NOT_APPLICABLE the established way
(PRD-027): CheckStatus.PASS, required=False, evidence.status NOT_APPLICABLE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from kriya.static_analysis.coverage import language_of
from kriya.static_analysis.model import MATURITY_RANK, ProviderProbe
from kriya.static_analysis.port import StaticAnalysisPort
from kriya.static_analysis.registry import ProviderNotRegisteredError, create_provider
from kriya.static_analysis.service import analysis_enabled, egress_admission, execution_context
from kriya.static_analysis.waivers import is_expired, load_waivers, waiver_store_path
from kriya.tools.containment import ContainmentSetupError

PASS, WARN, FAIL, NOT_APPLICABLE = "PASS", "WARN", "FAIL", "NOT_APPLICABLE"
CHECK_IDS = (
    "static_analysis.configuration", "static_analysis.provider", "static_analysis.capability",
    "static_analysis.coverage", "static_analysis.prerequisites", "static_analysis.waivers",
    "static_analysis.egress",
)
# Bounded repository walk for the language inventory.
_MAX_INVENTORY_FILES = 50_000
_SKIP_DIRS = frozenset({".git", ".kriya", ".hg", ".svn", "node_modules"})


@dataclass(frozen=True)
class Row:
    status: str
    evidence: Dict[str, Any]
    remediation: str = "No remediation required."


def rows_required(cfg: Any) -> bool:
    return analysis_enabled(cfg) and cfg.static_analysis.requirement == "required"


def repository_languages(workspace: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    seen = 0
    for _directory, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            language = language_of(name)
            if language is not None:
                counts[language] = counts.get(language, 0) + 1
            seen += 1
            if seen >= _MAX_INVENTORY_FILES:
                return counts
    return counts


def evaluate_rows(cfg: Any, workspace: str) -> Dict[str, Row]:
    """Every static-analysis doctor row, computed with one probe."""
    workspace = os.path.realpath(workspace)
    not_applicable = Row(NOT_APPLICABLE, {"status": NOT_APPLICABLE, "detail": "static analysis is disabled"})
    if not analysis_enabled(cfg):
        rows = {check_id: not_applicable for check_id in CHECK_IDS}
        rows["static_analysis.waivers"] = _waiver_row(cfg, workspace, required=False)
        return rows
    static = cfg.static_analysis
    required = static.requirement == "required"
    miss = FAIL if required else WARN
    rows: Dict[str, Row] = {}
    rows["static_analysis.configuration"] = (
        Row(WARN, {"provider": static.provider, "requirement": static.requirement,
                   "when_unavailable": static.policy.when_unavailable},
            "An enabled but optional scanner commits when the provider is unavailable; set requirement: required.")
        if not required and static.policy.when_unavailable == "warn"
        else Row(PASS, {"provider": static.provider, "requirement": static.requirement})
    )

    port: Optional[StaticAnalysisPort] = None
    probe: Optional[ProviderProbe] = None
    try:
        port = create_provider(
            static.provider, dict(static.providers.get(static.provider, {})), execution_context(cfg, workspace),
        )
        probe = port.probe()
    except (ProviderNotRegisteredError, ContainmentSetupError) as error:
        probe = ProviderProbe(identity=None, capability=None, reason_code=type(error).__name__, detail=str(error))
    if probe.identity is None or probe.capability is None:
        unavailable = Row(miss, {"reason_code": probe.reason_code, "detail": probe.detail},
                          "Install the configured provider version (and pinned image when contained) and fix its rule packs.")
        for check_id in ("static_analysis.provider", "static_analysis.capability", "static_analysis.coverage",
                         "static_analysis.prerequisites", "static_analysis.egress"):
            rows[check_id] = unavailable
        rows["static_analysis.waivers"] = _waiver_row(cfg, workspace, required=required)
        return rows

    identity, capability = probe.identity, probe.capability
    rows["static_analysis.provider"] = Row(PASS, {
        "provider": identity.provider, "version": identity.version, "edition": identity.edition,
        "execution_location": identity.execution_location, "identity_digest": identity.identity_digest,
    })
    ruled = {lang: s for lang, s in capability.languages.items() if s.rules_available > 0}
    rows["static_analysis.capability"] = (
        Row(miss, {"rule_packs": [p.to_dict() for p in identity.rule_packs]},
            "The configured rule packs target no language the provider supports.")
        if not ruled else
        Row(PASS, {"rule_packs": [p.to_dict() for p in identity.rule_packs],
                   "languages": {k: {"maturity": v.maturity, "rules": v.rules_available} for k, v in sorted(ruled.items())},
                   "limitations": list(capability.limitations)})
    )

    repo_languages = repository_languages(workspace)
    min_maturity = str(static.providers.get(static.provider, {}).get("min_language_maturity", "ga"))
    covered = {
        lang for lang, support in ruled.items()
        if MATURITY_RANK.get(support.maturity, -1) >= MATURITY_RANK[min_maturity]
    }
    uncovered = sorted(set(repo_languages) - covered)
    coverage_evidence = {"repository_languages": repo_languages, "covered": sorted(set(repo_languages) & covered),
                         "uncovered": uncovered}
    if not repo_languages:
        rows["static_analysis.coverage"] = Row(NOT_APPLICABLE, {"status": NOT_APPLICABLE, "detail": "no classified source files"})
    elif not uncovered:
        rows["static_analysis.coverage"] = Row(PASS, coverage_evidence)
    else:
        action = static.policy.partial_coverage if len(uncovered) < len(repo_languages) else static.policy.unsupported_language
        rows["static_analysis.coverage"] = Row(
            FAIL if action == "block" else WARN, coverage_evidence,
            f"Languages without coverage: {', '.join(uncovered)} - add rules or accept the policy outcome.",
        )

    prerequisites = list(port.check_prerequisites(capability, sorted(repo_languages), workspace)) if port else []
    unmet = [p for p in prerequisites if not p.met]
    if not prerequisites:
        rows["static_analysis.prerequisites"] = Row(NOT_APPLICABLE, {"status": NOT_APPLICABLE, "detail": "none declared"})
    elif not unmet:
        rows["static_analysis.prerequisites"] = Row(PASS, {"met": [p.__dict__ for p in prerequisites]})
    else:
        rows["static_analysis.prerequisites"] = Row(
            FAIL if static.policy.prerequisites_missing == "block" else WARN,
            {"unmet": [p.__dict__ for p in unmet]}, "Provide the provider's declared prerequisites.",
        )

    rows["static_analysis.waivers"] = _waiver_row(cfg, workspace, required=required)
    egress = egress_admission(capability, cfg.autonomy.egress_policy)
    if not egress["admitted"]:
        rows["static_analysis.egress"] = Row(FAIL, egress, "The provider needs network or source upload that the egress policy forbids.")
    elif identity.network_enforced:
        rows["static_analysis.egress"] = Row(PASS, {**egress, "network_enforced": True})
    else:
        rows["static_analysis.egress"] = Row(
            WARN, {**egress, "network_enforced": False},
            "Host execution: network discipline comes from the provider's fixed flags, not containment. "
            "Set autonomy.contained_execution_required with a pinned provider image.",
        )
    return rows


def _waiver_row(cfg: Any, workspace: str, *, required: bool) -> Row:
    try:
        path = waiver_store_path(cfg.static_analysis.waivers.store, workspace)
    except ValueError as error:
        return Row(FAIL if required else WARN, {"error": str(error)}, "Move the waiver store outside the workspace.")
    store = load_waivers(path, workspace)
    if store.status == "absent":
        return Row(NOT_APPLICABLE, {"status": NOT_APPLICABLE, "store": path, "detail": "no waivers recorded"})
    if store.status == "invalid":
        return Row(FAIL if required else WARN, {"store": path, "error": store.error},
                   "The waiver store is corrupt or was modified; no waiver from it applies. Repair or remove it.")
    expired = [r.waiver_id for r in store.records if is_expired(r)]
    never = [r.waiver_id for r in store.records if r.expires_at is None]
    evidence = {"store": path, "waivers": len(store.records), "expired": expired, "never_expiring": never}
    if expired or never:
        return Row(WARN, evidence, "Revoke expired waivers and give every waiver an expiry.")
    return Row(PASS, evidence)


def static_analysis_status_report(cfg: Any, workspace: str) -> Dict[str, Any]:
    static = cfg.static_analysis
    rows = evaluate_rows(cfg, workspace)
    return {
        "enabled": analysis_enabled(cfg), "provider": static.provider, "requirement": static.requirement,
        "scope": static.scope, "max_target_bytes": static.max_target_bytes, "exclusions": list(static.exclusions),
        "rows": {check_id: {"status": row.status, "evidence": row.evidence} for check_id, row in rows.items()},
    }

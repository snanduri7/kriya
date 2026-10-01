"""Language classification and coverage (PRD-031A §6).

Coverage is decided twice: before the scan, from the provider's declared
capability (language, maturity, rules, prerequisites), and after it, from
what the provider confirms it actually analyzed. A target is COVERED only
when both hold; "passed to the scanner" never means "analyzed".
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from kriya.static_analysis.model import (
    MATURITY_RANK,
    CoverageReport,
    CoverageStatus,
    PrerequisiteResult,
    ProviderCapability,
    ScanResult,
    ScopePlan,
    Side,
    TargetCoverage,
    TargetStatus,
)

# Kriya-owned, provider-neutral language ids. Derived from, but separate
# from, kriya/analyzer/analyzer.py's EXTENSION_MAP (display names). An
# extension not listed here is "unclassified": reported, never counted as
# analyzed or unanalyzed source.
LANGUAGE_BY_EXTENSION: Mapping[str, str] = {
    ".java": "java", ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rb": "ruby", ".kt": "kotlin", ".kts": "kotlin", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp", ".rs": "rust", ".php": "php", ".swift": "swift", ".scala": "scala",
}


def language_of(relpath: str) -> Optional[str]:
    return LANGUAGE_BY_EXTENSION.get(os.path.splitext(relpath)[1].lower())


def _capability_status(
    language: str, capability: ProviderCapability, min_maturity: str, missing_prerequisites: Set[str],
) -> Tuple[TargetStatus, str]:
    support = capability.languages.get(language)
    if support is None or MATURITY_RANK.get(support.maturity, -1) < MATURITY_RANK[min_maturity]:
        maturity = support.maturity if support is not None else "unsupported"
        return TargetStatus.UNSUPPORTED_LANGUAGE, f"provider support for {language}: {maturity}"
    if support.rules_available <= 0:
        return TargetStatus.NO_RULES, f"no configured rule targets {language}"
    if language in missing_prerequisites:
        return TargetStatus.PREREQUISITE_MISSING, f"prerequisite missing for {language}"
    return TargetStatus.COVERED, ""


def evaluate_coverage(
    plan: ScopePlan, capability: ProviderCapability, *, min_maturity: str,
    prerequisites: Sequence[PrerequisiteResult] = (),
) -> CoverageReport:
    """Pre-scan coverage from the declared capability. A target the scan
    must still confirm is provisionally COVERED here."""
    missing = {p.language for p in prerequisites if not p.met}
    sides: Dict[str, List[Side]] = {}
    for path in plan.pre_targets:
        sides.setdefault(path, []).append(Side.PRE)
    for path in plan.post_targets:
        sides.setdefault(path, []).append(Side.POST)
    targets: List[TargetCoverage] = []
    unclassified: Set[str] = set()
    for path in sorted(set(plan.pre_members) | set(plan.post_members)):
        language = language_of(path)
        if language is None:
            unclassified.add(path)
            continue
        if path not in sides:
            continue  # oversized on every side it exists on: recorded below
        status, detail = _capability_status(language, capability, min_maturity, missing)
        targets.append(TargetCoverage(path, language, status, tuple(sides[path]), detail))
    for oversized in plan.oversized:
        language = language_of(oversized.relpath)
        if language is None:
            continue
        targets = [t for t in targets if t.relpath != oversized.relpath]
        targets.append(TargetCoverage(
            oversized.relpath, language, TargetStatus.OVERSIZED, (oversized.side,),
            f"{oversized.size} bytes > limit {oversized.limit} ({oversized.side.value})",
        ))
    unclassified.update(c.relpath for c in plan.changed if language_of(c.relpath) is None)
    return _report(sorted(targets, key=lambda t: t.relpath), tuple(sorted(unclassified)))


def confirm_coverage(
    report: CoverageReport, scans: Mapping[Side, Optional[ScanResult]],
) -> CoverageReport:
    """Post-scan: a provisionally COVERED target the scanner of any side it
    was submitted to did not confirm becomes NOT_ANALYZED."""
    confirmed: List[TargetCoverage] = []
    for target in report.targets:
        if target.status is TargetStatus.COVERED:
            unconfirmed = [
                side for side in target.sides
                if scans.get(side) is None or target.relpath not in scans[side].analyzed
            ]
            if unconfirmed:
                reasons = []
                for side in unconfirmed:
                    scan = scans.get(side)
                    reason = scan.skipped.get(target.relpath) if scan is not None else None
                    reasons.append(f"{side.value}: {reason or 'not confirmed analyzed by the provider'}")
                target = TargetCoverage(
                    target.relpath, target.language, TargetStatus.NOT_ANALYZED, target.sides, "; ".join(reasons),
                )
        confirmed.append(target)
    return _report(confirmed, report.unclassified)


def _report(targets: Sequence[TargetCoverage], unclassified: Tuple[str, ...]) -> CoverageReport:
    return CoverageReport(status=aggregate_status(targets), targets=tuple(targets), unclassified=unclassified)


def aggregate_status(targets: Iterable[TargetCoverage]) -> CoverageStatus:
    statuses = [t.status for t in targets]
    if not statuses:
        return CoverageStatus.NOT_APPLICABLE
    uncovered = [s for s in statuses if s is not TargetStatus.COVERED]
    if not uncovered:
        return CoverageStatus.FULL
    if all(s is TargetStatus.PREREQUISITE_MISSING for s in uncovered):
        return CoverageStatus.PREREQUISITES_MISSING
    if len(uncovered) == len(statuses) and all(
        s in (TargetStatus.UNSUPPORTED_LANGUAGE, TargetStatus.NO_RULES) for s in uncovered
    ):
        return CoverageStatus.UNSUPPORTED
    return CoverageStatus.PARTIAL

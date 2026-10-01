"""Finding fingerprints and the PRE/POST diff (PRD-031A §8.2, §8.3).

A finding's ``location_key`` is (rule id, path, whitespace-normalized
matched source), taken from the snapshot file, so it survives line shifts
and re-indentation. The diff is a multiset comparison per location key over
the whole scope, including files the candidate did not change.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.static_analysis.model import (
    Classification,
    ClassifiedFinding,
    Finding,
    Side,
    canonical_digest,
)

_WHITESPACE = re.compile(r"\s+")


def normalized_snippet(root: str, finding: Finding, cache: Dict[str, List[str]]) -> str:
    """The matched lines of the snapshot file, whitespace-collapsed. Line
    numbers never enter the key; an unreadable file yields ""."""
    lines = cache.get(finding.path)
    if lines is None:
        try:
            with open(os.path.join(root, finding.path), "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            lines = []
        cache[finding.path] = lines
    start = max(finding.start_line, 1)
    end = max(finding.end_line, start)
    return _WHITESPACE.sub(" ", " ".join(lines[start - 1:end])).strip()


def location_key(finding: Finding, snippet: str) -> str:
    return canonical_digest([finding.rule_id, finding.path, snippet])


def _keyed(root: str, findings: Sequence[Finding]) -> Dict[str, List[Finding]]:
    cache: Dict[str, List[str]] = {}
    keyed: Dict[str, List[Finding]] = {}
    for finding in findings:
        key = location_key(finding, normalized_snippet(root, finding, cache))
        keyed.setdefault(key, []).append(finding)
    for group in keyed.values():
        group.sort(key=lambda f: (f.start_line, f.start_col, f.end_line, f.end_col))
    return keyed


def _fingerprint(key: str, occurrence: int) -> str:
    return canonical_digest([key, occurrence])


def diff_findings(
    pre_root: str, pre: Sequence[Finding], post_root: str, post: Sequence[Finding],
    *, comparable: bool = True,
) -> Tuple[ClassifiedFinding, ...]:
    """Classify every finding of both scans.

    Per location key with PRE count m and POST count n: min(m, n) are
    UNCHANGED, n - m extra are WORSENED when m > 0 (INTRODUCED when m == 0),
    m - n extra are RESOLVED. An unchanged key whose POST severity is higher
    is WORSENED. Not comparable (scanner identity differs): every POST
    finding is INTRODUCED, nothing is excused (PRD-024 NOT_COMPARABLE)."""
    post_keyed = _keyed(post_root, post)
    if not comparable:
        return tuple(
            ClassifiedFinding(f, _fingerprint(key, i), key, Classification.INTRODUCED, Side.POST)
            for key, group in sorted(post_keyed.items()) for i, f in enumerate(group)
        )
    pre_keyed = _keyed(pre_root, pre)
    classified: List[ClassifiedFinding] = []
    for key in sorted(set(pre_keyed) | set(post_keyed)):
        before = pre_keyed.get(key, [])
        after = post_keyed.get(key, [])
        worst_before: Optional[int] = max((f.severity.rank for f in before), default=None)
        for i, finding in enumerate(after):
            if i < len(before):
                worsened = worst_before is not None and finding.severity.rank > worst_before
                kind = Classification.WORSENED if worsened else Classification.UNCHANGED
            else:
                kind = Classification.WORSENED if before else Classification.INTRODUCED
            classified.append(ClassifiedFinding(finding, _fingerprint(key, i), key, kind, Side.POST))
        for i in range(len(after), len(before)):
            classified.append(ClassifiedFinding(before[i], _fingerprint(key, i), key, Classification.RESOLVED, Side.PRE))
    return tuple(classified)


def summary(classified: Iterable[ClassifiedFinding]) -> Dict[str, int]:
    counts = {c.value: 0 for c in Classification}
    for item in classified:
        counts[item.classification.value] += 1
    return counts

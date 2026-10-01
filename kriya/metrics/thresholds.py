"""Release-threshold evaluation mechanics (PRD-033, consumed by PRD-036).

No threshold values ship with Kriya: none are user-approved, so without an
operator file the evaluation is ``NOT_CONFIGURED``. The file is local,
operator-owned, and must live outside the workspace (a repository can never
lower its own release bar):

    schema_version: 1
    thresholds:
      - metric: adjudication.false_success_rate   # a dotted path into the report content
        comparator: max                           # value <= threshold passes; "min": value >= threshold
        value: 0.02
        min_samples: 30                           # the metric's denominator (or count) must reach this

Each threshold is PASS, FAIL, INSUFFICIENT_EVIDENCE (below min_samples) or
UNAVAILABLE (the metric has no evidence). The overall result is FAIL if any
threshold fails, INCONCLUSIVE if any lacks evidence, else PASS. UNKNOWN or
UNAVAILABLE is never PASS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from kriya.metrics.evidence import canonical_digest

THRESHOLDS_SCHEMA_VERSION = 1
COMPARATORS = ("max", "min")
PASS, FAIL = "PASS", "FAIL"
INSUFFICIENT_EVIDENCE, UNAVAILABLE = "INSUFFICIENT_EVIDENCE", "UNAVAILABLE"
NOT_CONFIGURED, INCONCLUSIVE = "NOT_CONFIGURED", "INCONCLUSIVE"


class ThresholdConfigError(ValueError):
    """The thresholds file is unreadable, malformed or names an unknown metric."""


@dataclass(frozen=True)
class Threshold:
    metric: str
    comparator: str
    value: float
    min_samples: int


def load_thresholds(path: str, *, workspace_root: Optional[str] = None) -> Tuple[Tuple[Threshold, ...], str]:
    """(thresholds, file digest). Refused inside ``workspace_root``."""
    if workspace_root is not None:
        from kriya.config.authority_approval import validate_trust_path_outside_workspace

        validate_trust_path_outside_workspace(os.path.realpath(path), workspace_root)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        payload = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as error:
        raise ThresholdConfigError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != THRESHOLDS_SCHEMA_VERSION:
        raise ThresholdConfigError(f"{path}: schema_version must be {THRESHOLDS_SCHEMA_VERSION}")
    entries = payload.get("thresholds")
    if not isinstance(entries, list) or not entries:
        raise ThresholdConfigError(f"{path}: thresholds must be a non-empty list")
    thresholds = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) - {"metric", "comparator", "value", "min_samples"}:
            raise ThresholdConfigError(f"{path}: threshold {index} has unknown or missing fields")
        metric, comparator, value = entry.get("metric"), entry.get("comparator"), entry.get("value")
        samples = entry.get("min_samples", 1)
        if (not isinstance(metric, str) or comparator not in COMPARATORS
                or not isinstance(value, (int, float)) or isinstance(value, bool)
                or not isinstance(samples, int) or isinstance(samples, bool) or samples < 1):
            raise ThresholdConfigError(f"{path}: threshold {index} is invalid")
        thresholds.append(Threshold(metric, comparator, float(value), samples))
    return tuple(thresholds), canonical_digest({"text": text})


def _metric(content: Mapping[str, Any], dotted: str) -> Mapping[str, Any]:
    node: Any = content
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise ThresholdConfigError(f"unknown metric {dotted!r}")
        node = node[part]
    if not isinstance(node, Mapping) or "status" not in node:
        raise ThresholdConfigError(f"{dotted!r} is not a metric")
    return node


def _samples(metric: Mapping[str, Any]) -> int:
    for key in ("denominator", "count", "value"):
        if isinstance(metric.get(key), int):
            return int(metric[key])
    return 0


def evaluate_thresholds(content: Mapping[str, Any], thresholds: Optional[Tuple[Threshold, ...]],
                        digest: Optional[str] = None) -> Dict[str, Any]:
    if not thresholds:
        return {"status": NOT_CONFIGURED}
    results: List[Dict[str, Any]] = []
    for threshold in thresholds:
        metric = _metric(content, threshold.metric)
        row: Dict[str, Any] = {"metric": threshold.metric, "comparator": threshold.comparator,
                               "threshold": threshold.value, "min_samples": threshold.min_samples}
        if metric.get("status") != "MEASURED" or not isinstance(metric.get("value"), (int, float)):
            row.update(status=UNAVAILABLE, reason=metric.get("reason"))
        elif _samples(metric) < threshold.min_samples:
            row.update(status=INSUFFICIENT_EVIDENCE, samples=_samples(metric))
        else:
            value = float(metric["value"])
            passed = value <= threshold.value if threshold.comparator == "max" else value >= threshold.value
            row.update(status=PASS if passed else FAIL, value=value, samples=_samples(metric))
        results.append(row)
    statuses = {row["status"] for row in results}
    overall = FAIL if FAIL in statuses else INCONCLUSIVE if statuses - {PASS} else PASS
    return {"status": overall, "thresholds_digest": digest, "results": results}


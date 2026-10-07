"""REG-R1: baseline field stability for the pytest per-test regression authority.

Invariant: a candidate may be blamed only for a difference that is stable on
the untouched baseline. When a test fails the same way (identity, outcome,
exception type) before and after the candidate but its message or failure
body differs, ``kriya/workflow/validation_baseline.py`` reports it as
``stability_required`` instead of blaming the candidate. This module then
measures, on the UNTOUCHED baseline only, whether those fields reproduce:

- observations = the original baseline observation + ``BASELINE_REPLAYS``
  fresh replays of exactly the disputed tests, each in its own fresh copy of
  the pristine workspace (whose content revision must equal the baseline's
  before and after the replays);
- a field identical across every observation is STABLE (a candidate that
  changes it is blamed); one that differs is VOLATILE (it loses authority
  for that test only, the remaining stable signature decides);
- anything that prevents an observation - no node id, a changed revision, a
  failed or incomplete replay, the test not failing the same way on the
  baseline - is INDETERMINATE, which keeps the comparison blocking without
  blaming the candidate (fail closed).

Candidate observations are never an input: volatility learned from the
candidate would let candidate-caused nondeterminism switch evidence off.

Measurements are cached per binding (pristine revision, test command,
selection, environment, runner version, test key, the baseline's own result
for that test, evidence and policy versions); any change is a different
binding and the measurement is taken again. The cache lives in the run state
and its checkpoint.

Every per-test pytest decision is retained as an LR-R1-M1 ``regression.decision``
record (comparison, per-test artifacts, stability observations); the raw
stdout/stderr/JUnit of every gate run, replays included, are already retained
by their ``gate.result`` records. Recording never changes a verdict.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Sequence, Tuple

from kriya.workflow.edit_safety import content_revision
from kriya.workflow.validation_baseline import (
    FIELD_INDETERMINATE,
    FIELD_STABLE,
    FIELD_VOLATILE,
    PYTEST_EVIDENCE_VERSION,
    STABILITY_FIELDS,
    BaselineDeltaResult,
    PytestCaseSignature,
    ValidationBaseline,
    ValidationOutcome,
    classify_baseline_delta,
    pytest_suite_evidence,
)

logger = logging.getLogger(__name__)

STABILITY_POLICY_VERSION = 1
BASELINE_REPLAYS = 2


@dataclass
class StabilityMeasurement:
    """``statuses``: test key -> field -> FIELD_*, for every disputed test.
    ``records``: one evidence entry per test (how it was decided)."""

    statuses: Dict[str, Dict[str, str]] = field(default_factory=dict)
    records: List[Dict[str, Any]] = field(default_factory=list)


def stability_binding(baseline: ValidationBaseline, case: PytestCaseSignature) -> str:
    """What a cached measurement for ``case`` is valid for."""
    evidence = baseline.outcome.pytest_evidence if baseline.outcome is not None else None
    return content_revision(json.dumps({
        "policy": STABILITY_POLICY_VERSION, "replays": BASELINE_REPLAYS, "evidence_version": PYTEST_EVIDENCE_VERSION,
        "workspace_revision": baseline.workspace_revision,
        "command_identity": baseline.invocation.command_identity,
        "selection_identity": baseline.invocation.selection_identity,
        "environment_fingerprint": baseline.invocation.environment_fingerprint,
        "runner": "pytest", "runner_version": evidence.runner_version if evidence is not None else None,
        "test_key": case.key, "baseline_result": case.identity(),
    }, sort_keys=True))


def _indeterminate(reason: str) -> Tuple[Dict[str, str], str]:
    return {name: FIELD_INDETERMINATE for name in STABILITY_FIELDS}, reason


def establish_baseline_stability(
    *, baseline: ValidationBaseline, required: Dict[str, Tuple[str, ...]],
    cache: MutableMapping[str, Dict[str, Any]],
    replay: Callable[[Sequence[str]], Dict[str, Any]],
    current_revision: Callable[[], Optional[str]],
) -> StabilityMeasurement:
    """Measure, on the untouched baseline only, whether each disputed test's
    message and body reproduce (see the module docstring). ``replay(node_ids)``
    runs exactly those tests on a fresh copy of the pristine workspace and
    returns the test-gate result; ``current_revision()`` is the pristine
    workspace's content revision now."""
    measurement = StabilityMeasurement()
    evidence = baseline.outcome.pytest_evidence if baseline.outcome is not None else None
    pre_cases = evidence.by_key() if evidence is not None else {}
    pending: Dict[str, Tuple[PytestCaseSignature, str]] = {}
    for key in sorted(required):
        case = pre_cases.get(key)
        if case is None:
            statuses, reason = _indeterminate("NOT_IN_BASELINE")
            measurement.statuses[key] = statuses
            measurement.records.append({"test": key, "source": "none", "reason": reason, "fields": statuses})
            continue
        binding = stability_binding(baseline, case)
        cached = cache.get(binding)
        if isinstance(cached, dict) and cached.get("binding") == binding and isinstance(cached.get("fields"), dict):
            measurement.statuses[key] = dict(cached["fields"])
            measurement.records.append({"test": key, "source": "cache", "binding": binding, "fields": cached["fields"]})
            continue
        if not case.node_id:
            statuses, reason = _indeterminate("NO_NODE_ID")
            measurement.statuses[key] = statuses
            measurement.records.append({"test": key, "source": "none", "reason": reason, "fields": statuses})
            continue
        pending[key] = (case, binding)
    if not pending:
        return measurement

    observations: Dict[str, List[Optional[PytestCaseSignature]]] = {key: [] for key in pending}
    replay_reports: List[Optional[str]] = []
    failure = None
    if current_revision() != baseline.workspace_revision:
        failure = "BASELINE_REVISION_CHANGED"
    else:
        node_ids = [case.node_id for case, _ in pending.values()]
        for _ in range(BASELINE_REPLAYS):
            try:
                result = replay(node_ids)
            except Exception as error:  # a broken replay is evidence of nothing
                failure = f"REPLAY_FAILED:{type(error).__name__}"
                break
            replayed = pytest_suite_evidence(result) if isinstance(result, dict) else None
            if replayed is None or not replayed.complete:
                failure = f"REPLAY_EVIDENCE_INCOMPLETE:{replayed.reason if replayed is not None else 'MISSING'}"
                break
            replay_reports.append(replayed.report_digest)
            by_key = replayed.by_key()
            for key in pending:
                observations[key].append(by_key.get(key))
        if failure is None and current_revision() != baseline.workspace_revision:
            failure = "BASELINE_REVISION_CHANGED"
    for key, (case, binding) in pending.items():
        seen = observations[key]
        if failure is not None:
            statuses, reason = _indeterminate(failure)
        elif any(o is None or o.outcome != case.outcome or o.failure_type != case.failure_type for o in seen):
            statuses, reason = _indeterminate("BASELINE_REPLAY_OUTCOME_DIFFERS")
        else:
            statuses = {name: FIELD_STABLE if all(o.field_digest(name) == case.field_digest(name) for o in seen)
                        else FIELD_VOLATILE for name in STABILITY_FIELDS}
            reason = None
            cache[binding] = {"binding": binding, "test": key, "node_id": case.node_id, "fields": statuses,
                              "observations": [case.identity()] + [o.identity() for o in seen],
                              "replay_reports": list(replay_reports)}
        measurement.statuses[key] = statuses
        measurement.records.append({
            "test": key, "node_id": case.node_id, "source": "replay", "binding": binding, "reason": reason,
            "fields": statuses, "baseline_observation": case.identity(),
            "replay_observations": [o.identity() if o is not None else None for o in seen],
            "replay_reports": list(replay_reports),
        })
    return measurement


def classify_with_baseline_stability(
    *, baseline: ValidationBaseline, post: ValidationOutcome, post_environment: Optional[str],
    cache: MutableMapping[str, Dict[str, Any]],
    replay: Callable[[Sequence[str]], Dict[str, Any]],
    current_revision: Callable[[], Optional[str]],
) -> Tuple[BaselineDeltaResult, Optional[StabilityMeasurement]]:
    """``classify_baseline_delta``, then - only when per-test authority
    disputes a message/body - the baseline stability of exactly those tests,
    and the final classification with it."""
    delta = classify_baseline_delta(baseline, post, post_environment=post_environment)
    if not delta.stability_required:
        return delta, None
    measurement = establish_baseline_stability(
        baseline=baseline, required=delta.stability_required, cache=cache, replay=replay,
        current_revision=current_revision)
    return (classify_baseline_delta(baseline, post, post_environment=post_environment,
                                    stability=measurement.statuses), measurement)


_COPY_EXCLUDED_DIRS = frozenset({".git", ".kriya", ".pytest_cache", "__pycache__", "node_modules",
                                 "target", "build", "dist"})


def _copy_pristine(source: str, dest: str) -> None:
    """Project content of ``source`` into ``dest`` (control-plane and build
    output excluded, as the other baseline replays do). Unlike a diagnostic
    copy it never skips a file it cannot copy: an incomplete copy would be
    a different baseline, so the error propagates (the replay is then
    INDETERMINATE)."""
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(name for name in dirs if name not in _COPY_EXCLUDED_DIRS)
        relative = os.path.relpath(root, source)
        target = dest if relative == "." else os.path.join(dest, relative)
        os.makedirs(target, exist_ok=True)
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue  # never followed out of the workspace (same rule as the other replays)
            shutil.copy2(path, os.path.join(target, name))


def replay_on_untouched_baseline(node_ids: Sequence[str], *, workspace_path: str, autonomy_cfg: Any) -> Dict[str, Any]:
    """Run exactly ``node_ids`` with the production test gate on a fresh copy
    of the pristine workspace (never the workspace itself); the copy is
    always removed."""
    from kriya.tools.validate import PolymorphicValidator

    scratch = tempfile.mkdtemp(prefix="kriya-stability-replay-")
    try:
        _copy_pristine(workspace_path, scratch)
        validator = PolymorphicValidator(scratch, original_workspace_path=scratch, autonomy_cfg=autonomy_cfg)
        return validator.run_tests(target_test=list(node_ids))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def decision_summary(delta: BaselineDeltaResult, measurement: Optional[StabilityMeasurement]) -> Dict[str, Any]:
    """The run-event view of a per-test decision (identifiers and statuses only)."""
    return {
        "authority": delta.authority,
        "pytest_evidence_status": delta.pytest_evidence_status,
        "diagnostic_level1": delta.level1.classification.value,
        "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()},
        "stability": ([{k: r.get(k) for k in ("test", "source", "reason", "fields")} for r in measurement.records]
                      if measurement is not None else []),
    }


def record_regression_decision(
    scope: str, baseline: ValidationBaseline, post: ValidationOutcome, delta: BaselineDeltaResult,
    measurement: Optional[StabilityMeasurement],
) -> None:
    """Retain one regression decision as an LR-R1-M1 ``regression.decision``
    record - observational: it never raises and never changes the decision."""
    from kriya.core.attempt_evidence import scope as attempt_evidence_scope

    try:
        if attempt_evidence_scope.capture_mode() is None:
            return
        pre = baseline.outcome
        payload = {
            "scope": scope, "authority": delta.authority, "blocking": delta.blocking,
            "blocking_reasons": list(delta.blocking_reasons), "diagnostic_level1": delta.level1.classification.value,
            "pytest_evidence_status": delta.pytest_evidence_status,
            "baseline_report": pre.pytest_evidence.report_digest if pre and pre.pytest_evidence else None,
            "post_report": post.pytest_evidence.report_digest if post.pytest_evidence else None,
            "stability_measured": len(measurement.records) if measurement is not None else 0,
        }
        content = {
            "comparison": json.dumps({
                "level1": {"classification": delta.level1.classification.value,
                           "pre_fingerprint": delta.level1.pre_fingerprint,
                           "post_fingerprint": delta.level1.post_fingerprint},
                "level2": {k: v.value for k, v in delta.level2.items()},
                "stability_required": {k: list(v) for k, v in delta.stability_required.items()},
                "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()},
                "aggregate_drop_detected": delta.aggregate_drop_detected,
            }, sort_keys=True),
            "stability": json.dumps(measurement.records if measurement is not None else [], sort_keys=True),
        }
        if pre is not None and pre.pytest_evidence is not None:
            content["baseline_cases"] = json.dumps(pre.pytest_evidence.to_dict(), sort_keys=True)
        if post.pytest_evidence is not None:
            content["post_cases"] = json.dumps(post.pytest_evidence.to_dict(), sort_keys=True)
        attempt_evidence_scope.emit("regression.decision", payload, content=content)
    except Exception as error:  # observational: never alters the decision
        logger.warning("Attempt evidence: regression.decision not recorded (%s)", error)

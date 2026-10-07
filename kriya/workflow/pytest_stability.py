"""REG-R1: baseline field stability for the pytest per-test regression authority.

Invariant: a candidate may be blamed only for a difference that is stable on
the untouched baseline - and stability is measured in the SAME verification
context that produced the disputed evidence.

When a test fails the same way (identity, outcome, exception type) before and
after the candidate but its message or failure body differs,
``kriya/workflow/validation_baseline.py`` reports it as ``stability_required``
instead of blaming the candidate. This module then re-runs the baseline's own
verification - the exact gate call that captured the baseline (same
workspace, working directory, runtime, environment, command and test
selection; a full-suite baseline is replayed as the full suite, never as the
disputed test alone) - ``BASELINE_REPLAYS`` times on the untouched baseline:

- the pristine revision must equal the baseline's before and after the
  replays, and each replay's pytest session facts (versions, root, config,
  test paths, plugin set) must equal the baseline's; otherwise every disputed
  field is UNRESOLVED;
- per disputed test, from the original observation plus the replays:
  outcome and exception type must be identical in all of them - a baseline
  that changes outcome (BASELINE_OUTCOME_UNSTABLE) or type
  (BASELINE_TYPE_UNSTABLE) in the same context is never permission: the
  message and body are UNRESOLVED and the comparison fails closed; with both
  stable, a message/body identical in every observation is STABLE (a
  candidate that changes it is blamed) and one that differs is VOLATILE (it
  loses authority for that test only).

Candidate observations are never an input. The replays are taken once per
verification context (``verification_context_id``: policy, evidence and
runner versions, pytest session facts, pristine revision, working directory,
command, selection, environment, baseline run and report) - not once per
test - and every disputed test of that context is judged from the same
replay reports. The measurement is cached under that id in the run state and
its checkpoint; any change of the context is a different id.

Every per-test pytest decision is retained as an LR-R1-M1 ``regression.decision``
record (comparison, per-test artifacts, stability observations); the raw
stdout/stderr/JUnit of every gate run, replays included, are retained by their
``gate.result`` records. Recording never changes a verdict.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Sequence, Tuple

from kriya.workflow.edit_safety import content_revision
from kriya.workflow.validation_baseline import (
    FIELD_STABLE,
    FIELD_UNRESOLVED,
    FIELD_VOLATILE,
    PYTEST_EVIDENCE_VERSION,
    STABILITY_FIELDS,
    BaselineDeltaResult,
    PytestCaseSignature,
    PytestSuiteEvidence,
    ValidationBaseline,
    ValidationOutcome,
    classify_baseline_delta,
    pytest_suite_evidence,
)

logger = logging.getLogger(__name__)

STABILITY_POLICY_VERSION = 2   # 2: same-context replays (1 replayed the disputed tests alone)
BASELINE_REPLAYS = 2
BASELINE_OUTCOME_UNSTABLE = "BASELINE_OUTCOME_UNSTABLE"
BASELINE_TYPE_UNSTABLE = "BASELINE_TYPE_UNSTABLE"
_STATE_FIELDS = ("outcome", "failure_type", *STABILITY_FIELDS)


@dataclass
class StabilityMeasurement:
    """``statuses``: test key -> {"message", "body"} -> FIELD_*, for every
    disputed test. ``records``: one evidence entry per test."""

    context_id: Optional[str] = None
    statuses: Dict[str, Dict[str, str]] = field(default_factory=dict)
    records: List[Dict[str, Any]] = field(default_factory=list)


def verification_context(baseline: ValidationBaseline, *, working_directory: str) -> Dict[str, Any]:
    """The facts that define where and how the baseline's verification ran
    (never a value that changes from one run of it to the next)."""
    evidence = baseline.outcome.pytest_evidence if baseline.outcome is not None else None
    return {
        "policy": STABILITY_POLICY_VERSION, "replays": BASELINE_REPLAYS, "evidence_version": PYTEST_EVIDENCE_VERSION,
        "runner": "pytest", "runner_version": evidence.runner_version if evidence is not None else None,
        "session": [list(fact) for fact in evidence.session] if evidence is not None else None,
        "workspace_revision": baseline.workspace_revision, "working_directory": working_directory,
        "command_identity": baseline.invocation.command_identity,
        "selection_identity": baseline.invocation.selection_identity,
        "target_test": list(baseline.invocation.target_test) if baseline.invocation.target_test is not None else None,
        "environment_fingerprint": baseline.invocation.environment_fingerprint,
        "baseline": {"run_id": baseline.run_id, "report": evidence.report_digest if evidence is not None else None},
    }


def verification_context_id(context: Dict[str, Any]) -> str:
    return content_revision(json.dumps(context, sort_keys=True))


def _measure_context(
    baseline: ValidationBaseline, context: Dict[str, Any], context_id: str,
    replay: Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]], current_revision: Callable[[], Optional[str]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Run the baseline's own verification ``BASELINE_REPLAYS`` times on the
    untouched baseline: (cache entry, None) or (None, why it failed)."""
    evidence = baseline.outcome.pytest_evidence
    if current_revision() != baseline.workspace_revision:
        return None, "BASELINE_REVISION_CHANGED"
    replays: List[PytestSuiteEvidence] = []
    for _ in range(BASELINE_REPLAYS):
        try:
            result = replay(baseline.invocation.target_test)
        except Exception as error:  # a broken replay is evidence of nothing
            return None, f"REPLAY_FAILED:{type(error).__name__}"
        replayed = pytest_suite_evidence(result) if isinstance(result, dict) else None
        if replayed is None or not replayed.complete:
            return None, f"REPLAY_EVIDENCE_INCOMPLETE:{replayed.reason if replayed is not None else 'MISSING'}"
        if replayed.session != evidence.session:
            return None, "REPLAY_CONTEXT_MISMATCH"
        replays.append(replayed)
    if current_revision() != baseline.workspace_revision:
        return None, "BASELINE_REVISION_CHANGED"
    return {"context_id": context_id, "context": context, "replays": [r.to_dict() for r in replays], "tests": {}}, None


def classify_baseline_observations(original: PytestCaseSignature,
                                   replayed: Sequence[Optional[PytestCaseSignature]]) -> Dict[str, Any]:
    """One test's field stability from its original baseline observation and
    its same-context replays."""
    observed = [original, *replayed]
    if any(o is None for o in observed):
        states: Dict[str, Any] = {"outcome": FIELD_UNRESOLVED, "failure_type": FIELD_UNRESOLVED}
        reason: Optional[str] = "TEST_ABSENT_FROM_REPLAY"
    else:
        states = {"outcome": FIELD_STABLE if len({o.outcome for o in observed}) == 1 else BASELINE_OUTCOME_UNSTABLE,
                  "failure_type": FIELD_STABLE if len({o.failure_type for o in observed}) == 1
                  else BASELINE_TYPE_UNSTABLE}
        reason = next((state for state in states.values() if state != FIELD_STABLE), None)
    for name in STABILITY_FIELDS:
        if reason is not None:
            states[name] = FIELD_UNRESOLVED
        else:
            states[name] = FIELD_STABLE if len({o.field_digest(name) for o in observed}) == 1 else FIELD_VOLATILE
    return {**states, "reason": reason,
            "observations": [o.identity() if o is not None else None for o in observed]}


def establish_baseline_stability(
    *, baseline: ValidationBaseline, required: Dict[str, Tuple[str, ...]],
    cache: MutableMapping[str, Dict[str, Any]],
    replay: Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]],
    current_revision: Callable[[], Optional[str]], working_directory: str,
) -> StabilityMeasurement:
    """Measure, in the baseline's own verification context and on the
    untouched baseline only, whether each disputed test's message and body
    reproduce (see the module docstring). ``replay(selection)`` is the
    baseline's own gate call; ``current_revision()`` is the pristine
    workspace's content revision now."""
    context = verification_context(baseline, working_directory=working_directory)
    context_id = verification_context_id(context)
    measurement = StabilityMeasurement(context_id=context_id)
    entry = cache.get(context_id)
    source, failure = "cache", None
    if not (isinstance(entry, dict) and entry.get("context_id") == context_id
            and len(entry.get("replays") or ()) == BASELINE_REPLAYS):
        source = "replay"
        entry, failure = _measure_context(baseline, context, context_id, replay, current_revision)
        if entry is not None:
            cache[context_id] = entry
    pre_cases = baseline.outcome.pytest_evidence.by_key()
    replayed = [PytestSuiteEvidence.from_dict(r).by_key() for r in entry["replays"]] if entry is not None else []
    for key in sorted(required):
        if failure is not None or key not in pre_cases:
            result: Dict[str, Any] = {name: FIELD_UNRESOLVED for name in _STATE_FIELDS}
            result["reason"] = failure or "NOT_IN_BASELINE"
        else:
            result = classify_baseline_observations(pre_cases[key], [r.get(key) for r in replayed])
            entry["tests"][key] = result
        measurement.statuses[key] = {name: result[name] for name in STABILITY_FIELDS}
        measurement.records.append({"test": key, "context_id": context_id, "source": source, **result})
    return measurement


def classify_with_baseline_stability(
    *, baseline: ValidationBaseline, post: ValidationOutcome, post_environment: Optional[str],
    cache: MutableMapping[str, Dict[str, Any]],
    replay: Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]],
    current_revision: Callable[[], Optional[str]], working_directory: str,
) -> Tuple[BaselineDeltaResult, Optional[StabilityMeasurement]]:
    """``classify_baseline_delta``, then - only when per-test authority
    disputes a message/body - the same-context baseline stability of those
    tests, and the final classification with it."""
    delta = classify_baseline_delta(baseline, post, post_environment=post_environment)
    if not delta.stability_required:
        return delta, None
    measurement = establish_baseline_stability(
        baseline=baseline, required=delta.stability_required, cache=cache, replay=replay,
        current_revision=current_revision, working_directory=working_directory)
    return (classify_baseline_delta(baseline, post, post_environment=post_environment,
                                    stability=measurement.statuses), measurement)


def decision_summary(delta: BaselineDeltaResult, measurement: Optional[StabilityMeasurement]) -> Dict[str, Any]:
    """The run-event view of a per-test decision (identifiers and states only)."""
    return {
        "authority": delta.authority,
        "pytest_evidence_status": delta.pytest_evidence_status,
        "diagnostic_level1": delta.level1.classification.value,
        "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()},
        "stability_context_id": measurement.context_id if measurement is not None else None,
        "stability": ([{k: r.get(k) for k in ("test", "source", "reason", *_STATE_FIELDS)}
                       for r in measurement.records] if measurement is not None else []),
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
            "stability_context_id": measurement.context_id if measurement is not None else None,
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

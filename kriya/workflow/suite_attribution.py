"""CANDIDATE-GATE-BASELINE-POLICY-001 (BACKEND-FINAL-CLOSURE-005 cohort 2, C2-S2_A / C2-S6_A): the one owner of
"does this full-suite result block, relative to the frozen PRE-mutation baseline".

Measured live: one pre-existing failing test was PRE_EXISTING_FAILURE / not blocking for a unit's terminal
full-regression check (kriya/workflow/workflow.py, PRD-024 attribution) and blocked every attempt of the next unit's
candidate gate (kriya/workflow/attempt.py judged ``run_tests()["success"]`` raw) in the same run. Two gates, one
failure, two verdicts. Every full-suite verdict now comes from here - the terminal check, the candidate gate's
full-suite runs, the verification coordinator - with the existing delta / stability-envelope semantics
(kriya/workflow/validation_baseline.py, kriya/workflow/pytest_stability.py) unchanged: a NEW / CHANGED failure, an
unexplained disappearance, a NOT_COMPARABLE environment or unavailable per-test evidence block exactly as before;
only failures the frozen baseline already carried, stable in the baseline's own context, do not. Without a captured
baseline a caller keeps its raw rule (nothing here guesses a baseline).

Pure decision + evidence: no subprocess, no model; the replay callable a caller passes is the baseline's own
verification call (REG-R1), executed only when a disputed test's stability must be measured.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.pytest_stability import (
    StabilityMeasurement,
    classify_with_baseline_stability,
    decision_summary,
    record_regression_decision,
)
from kriya.workflow.validation_baseline import (
    BaselineDeltaResult,
    DeltaClassification,
    ValidationBaseline,
    build_validation_outcome,
    render_blocking_regression_evidence,
)

logger = logging.getLogger(__name__)

SCOPE_FULL_REGRESSION = "full_regression"
SCOPE_CANDIDATE_GATE = "candidate_gate"
SCOPE_VERIFICATION_UNIT = "verification_unit"
SCOPE_TEST_DELTA_COVERING_RUN = "test_delta_covering_run"

_PRE_EXISTING_LOG = ("%s suite failed, but every failure is classified PRE_EXISTING_FAILURE relative to the captured "
                     "PRE-mutation baseline - not attributed to this candidate, not blocking.")


@dataclass
class SuiteAttribution:
    """One full-suite result judged against the frozen baseline."""

    scope: str
    suite_success: bool
    blocking: bool
    delta: BaselineDeltaResult
    stability: Optional[StabilityMeasurement]
    pre_environment: Optional[str]
    post_environment: Optional[str]

    @property
    def pre_existing_only(self) -> bool:
        """The suite failed and nothing is attributed to the candidate."""
        return not self.suite_success and not self.blocking

    def confirmed_regressions(self) -> List[str]:
        """Per-test NEW / CHANGED failures the delta attributes to the candidate."""
        return sorted(test_id for test_id, cls in self.delta.level2.items()
                      if cls in (DeltaClassification.NEW_FAILURE, DeltaClassification.CHANGED_FAILURE))

    def event_details(self) -> Dict[str, Any]:
        """The ``validation_baseline.*_delta`` run-event details (the terminal check's established shape)."""
        return {
            "level1_classification": self.delta.level1.classification.value,
            "level2": {k: v.value for k, v in self.delta.level2.items()},
            "aggregate_drop_detected": self.delta.aggregate_drop_detected,
            "blocking": self.blocking,
            "blocking_reasons": list(self.delta.blocking_reasons),
            # PRD-024: per-test comparison availability, never an empty result read as "no failures".
            "level2_available": self.delta.level2_available,
            "level2_unavailable_reason": self.delta.level2_unavailable_reason,
            "pre_environment": self.pre_environment,
            "post_environment": self.post_environment,
            **decision_summary(self.delta, self.stability),
        }

    def gate_evidence(self) -> Dict[str, Any]:
        """What a gate outcome records about this decision (content-free)."""
        return {"scope": self.scope, "suite_success": self.suite_success,  # the raw verdict stays readable (review F2)
                "blocking": self.blocking, "pre_existing_only": self.pre_existing_only,
                "level1_classification": self.delta.level1.classification.value,
                "confirmed_regressions": self.confirmed_regressions(),
                "blocking_reasons": list(self.delta.blocking_reasons)}

    def evidence_text(self, baseline: ValidationBaseline, raw_output: str) -> str:
        """The Developer-facing failure text: the confirmed regressions' own sections when per-test evidence
        attributes them (VAL-001 G1-DEVINV2), else the raw output."""
        confirmed = self.confirmed_regressions()
        if not confirmed:
            return raw_output
        return render_blocking_regression_evidence(baseline=baseline, confirmed_test_ids=confirmed, raw_output=raw_output)

    def message(self) -> str:
        return f"PRE/POST delta: level1={self.delta.level1.classification.value} blocking={self.blocking}"


def captured_baseline(baseline: Any) -> Optional[ValidationBaseline]:
    """The baseline when it carries real PRE evidence, else None (an indeterminate or absent baseline never
    excuses anything - the caller keeps its raw rule)."""
    if baseline is not None and getattr(baseline, "status", None) == "captured" and baseline.outcome is not None:
        return baseline
    return None


def post_environment_identity(
    *, baseline: ValidationBaseline, workspace_path: str, worktree_path: str, autonomy_cfg: Any, goal: str,
    written_files: Iterable[str],
) -> Optional[str]:
    """PRD-024: the POST run's environment identity over the candidate's toolchain declarations; None when the
    baseline recorded none (then nothing is compared) or the identity cannot be computed (stated, logged)."""
    if baseline.invocation.environment_fingerprint is None:
        return None
    from kriya.tools.toolchain_identity import ALL_TOOLCHAIN_DECLARATION_FILES
    from kriya.workflow.baseline_policy import baseline_environment_identity

    declarations: Dict[str, str] = {}
    written = set(written_files)
    for path in ALL_TOOLCHAIN_DECLARATION_FILES:
        if path in written:
            try:
                with open(os.path.join(worktree_path, path), "r", encoding="utf-8") as handle:
                    declarations[path] = handle.read()
            except OSError:
                pass
    try:
        return baseline_environment_identity(workspace_path, autonomy_cfg, goal=goal, candidate_files=declarations or None)
    except Exception as exc:
        logger.warning(f"POST environment identity unavailable: {exc}")
        return None


def attribute_suite_result(
    *, scope: str, baseline: ValidationBaseline, result: Mapping[str, Any], post_environment: Optional[str],
    stability_cache: MutableMapping[str, Dict[str, Any]],
    replay: Callable[[Optional[Tuple[str, ...]]], Dict[str, Any]], workspace_path: str,
) -> SuiteAttribution:
    """Judge one full-suite ``result`` against the captured ``baseline`` (REG-R1: a disputed test's stability is
    measured on the untouched baseline through ``replay``); the decision is retained as an LR-R1-M1
    ``regression.decision`` record under ``scope``."""
    post = build_validation_outcome(dict(result))
    delta, stability = classify_with_baseline_stability(
        baseline=baseline, post=post, post_environment=post_environment, cache=stability_cache, replay=replay,
        current_revision=lambda: compute_workspace_content_hash(workspace_path),
        working_directory=os.path.realpath(workspace_path),
    )
    record_regression_decision(scope, baseline, post, delta, stability)
    attribution = SuiteAttribution(
        scope=scope, suite_success=bool(result.get("success")), blocking=delta.blocking, delta=delta,
        stability=stability, pre_environment=baseline.invocation.environment_fingerprint,
        post_environment=post_environment,
    )
    if attribution.pre_existing_only:
        logger.info(_PRE_EXISTING_LOG, scope.replace("_", " ").capitalize())
    return attribution

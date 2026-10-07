"""QUALIFICATION-ARTIFACT-STABILITY-001: the runtime identity a qualification
run attributes its evidence to must stay the same for the whole run.

Measured 2026-10-07 (Ollama 0.40.0 requalification): the qwen3.6 tag was
rewritten by the provider's automatic conversion while ``kriya model
qualify`` was alive. Qualification probed the fingerprint once, ran every
case against whatever the tag served at the time, and sealed all of them
under the first digest - evidence of artifact B attributed to artifact A.

This module owns exactly four things: the baseline identity, the identity
observations taken at each qualification boundary, their comparison, and
the artifact-change decision. It is not a provider abstraction: the identity
compared is ``ModelRuntimeFingerprint.digest`` (PRD-013), which already
excludes timestamps, probe errors and every other volatile value, so a
provider reload that serves the same artifact is the same identity and a
different served artifact, weights blob, provider version, parser or window
is not. An observer that cannot re-observe the identity (a non-exact
fingerprint) yields another digest and therefore fails closed.

The decision is sticky: once another identity has been observed the run is
invalid, and a later observation equal to the baseline does not restore it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.core.model_runtime import ModelRuntimeFingerprint

ARTIFACT_CHANGED = "ARTIFACT_CHANGED"

# The observable boundaries of one qualification run, in order of occurrence.
START = "start"
BEFORE_CASE = "before_case"
AFTER_CASE = "after_case"
CLOSE = "close"
BOUNDARIES: Tuple[str, ...] = (START, BEFORE_CASE, AFTER_CASE, CLOSE)

IdentityObserver = Callable[[], ModelRuntimeFingerprint]


@dataclass(frozen=True)
class IdentityObservation:
    """One re-observation of the served runtime identity. ``observed_at`` is
    evidence for correlation with provider logs, never part of any identity."""
    index: int
    boundary: str
    capability: Optional[str]
    digest: str
    exact: bool
    observed_at: str
    probe_errors: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        record = asdict(self)
        record["probe_errors"] = list(self.probe_errors)
        return record


@dataclass(frozen=True)
class IdentityChange:
    """The first observation whose identity differs from the baseline, with
    the cases whose evidence had already been attributed before it."""
    boundary: str
    capability: Optional[str]
    baseline_digest: str
    observed_digest: str
    observation_index: int
    cases_completed: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        record = asdict(self)
        record["cases_completed"] = list(self.cases_completed)
        return record


class QualificationIdentityGuard:
    """Re-observes the runtime identity at every boundary of one qualification
    run and decides whether the run's evidence may still be attributed to its
    baseline. One guard per run; one comparison rule for every boundary."""

    def __init__(self, baseline: ModelRuntimeFingerprint, observe: IdentityObserver) -> None:
        self.baseline_digest = baseline.digest
        self._observe = observe
        self.observations: List[IdentityObservation] = []
        self.completed: List[str] = []
        self.change: Optional[IdentityChange] = None

    @property
    def stable(self) -> bool:
        return self.change is None

    def observe(self, boundary: str, capability: Optional[str] = None) -> Optional[IdentityChange]:
        """Take one observation at ``boundary`` and return the change that
        invalidates the run, if any. Once a change was observed it is returned
        for every later call without observing again: returning to the
        baseline identity does not restore trust."""
        if boundary not in BOUNDARIES:
            raise ValueError(f"unknown qualification boundary {boundary!r}")
        if self.change is not None:
            return self.change
        fingerprint = self._observe()
        observation = IdentityObservation(
            index=len(self.observations), boundary=boundary, capability=capability, digest=fingerprint.digest,
            exact=fingerprint.exact, observed_at=datetime.now(timezone.utc).isoformat(),
            probe_errors=tuple(fingerprint.probe_errors),
        )
        self.observations.append(observation)
        if observation.digest != self.baseline_digest:
            self.change = IdentityChange(
                boundary=boundary, capability=capability, baseline_digest=self.baseline_digest,
                observed_digest=observation.digest, observation_index=observation.index,
                cases_completed=tuple(self.completed),
            )
        return self.change

    def case_completed(self, capability: str) -> None:
        """Record that ``capability``'s evidence was attributed (its pre- and
        post-case observations both matched the baseline)."""
        self.completed.append(capability)

    def evidence(self) -> Dict[str, Any]:
        """The sealed identity evidence of this run: the baseline, every
        observation in order, and the change that invalidated it, if any."""
        return {
            "baseline_digest": self.baseline_digest,
            "stable": self.stable,
            "observations": [observation.to_dict() for observation in self.observations],
            "cases_completed": list(self.completed),
            "change": self.change.to_dict() if self.change is not None else None,
        }


__all__ = [
    "AFTER_CASE", "ARTIFACT_CHANGED", "BEFORE_CASE", "BOUNDARIES", "CLOSE", "START",
    "IdentityChange", "IdentityObservation", "IdentityObserver", "QualificationIdentityGuard",
]

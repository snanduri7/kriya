"""Risk, route and process-profile vocabulary owned by AUTHORITY (full modularization, slice C2).

Moved verbatim from ``kriya/workflow/triage.py`` (``ChangeKind``, ``RiskClass``, ``ExecutionWeight``, ``ImpactVector``,
``EngineeringRoute`` and, because ``EngineeringRoute.with_recomputed_risk`` calls it, ``determine_execution_weight`` with its
kind x risk table) and ``kriya/workflow/process_profile.py`` (``ContextDepth``, ``VerificationTier``, ``ProcessProfile``). These are
the types the policy layer reasons over (``policy.execution``, ``policy.model``) and STATE persists (``control.state``); the
classification logic that PRODUCES them (``EngineeringTriageService``, ``escalate_risk``, ``determine_risk_class``) and the profile
table (``LIGHT/STANDARD/HEAVY_PROFILE``, ``process_profile_for``) stay in the kernel modules, which re-export the names moved here
(removed in P1). Docstrings below are unchanged from their old home: "this module"/"below" in them refer to the old file.

Persisted forms are unchanged: the vocabulary is persisted only through ``to_dict()`` (enum ``.value``/``.name``); no module path is
recorded anywhere (tests/test_risk_vocabulary_persistence.py pins the bytes against a golden written before the move).
"""

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional


class ChangeKind(str, Enum):
    """What SHAPE the requested change is - never how risky it is. Settled
    by request text/repo-state signals (MA1.2), independently of risk_class
    below. A `task` can be `risk_class: high` (e.g. a one-line auth-bypass
    fix) without ever becoming a different kind - shape and danger are
    orthogonal axes by design, not a single score."""

    TASK = "task"
    ENHANCEMENT = "enhancement"
    MILESTONE = "milestone"
    REFACTOR = "refactor"


class RiskClass(IntEnum):
    """Ordered so `max()` over a sequence of RiskClass values does the right
    thing directly - this is what makes max_observed_risk_class's monotonic
    "never automatically lowered" invariant a one-line max() rather than a
    hand-written comparison table."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class ExecutionWeight(str, Enum):
    """How much of the pipeline actually runs for this request - the thing
    every downstream stage (context breadth, planning rigor, verification
    tier, approval gating) is meant to read, per kind x risk_class, never
    kind alone. See determine_execution_weight() below."""

    LIGHT = "light"
    STANDARD = "standard"
    HEAVY = "heavy"


@dataclass(frozen=True)
class ImpactVector:
    """Ten deterministic, tool-checkable components risk_class is computed
    from - never file count alone, and never asked of a model. Every field
    here is meant to come from real signals (the local index, the artifact/
    dependency graph, git, pattern matching against the request and touched
    paths) computed by EngineeringTriageService (MA1.2) - this dataclass
    itself has no opinion on how its fields get populated, only on what they
    mean once they are."""

    files_touched: int = 0
    symbols_impacted: int = 0
    downstream_references: int = 0

    public_contract_change: bool = False
    persistence_change: bool = False
    security_boundary_change: bool = False
    dependency_change: bool = False
    configuration_change: bool = False
    build_system_change: bool = False
    shared_entrypoint_change: bool = False


@dataclass(frozen=True)
class EngineeringRoute:
    """The full classification result for one generation request. Frozen
    and immutable by construction - escalate_risk() below returns a NEW
    EngineeringRoute rather than mutating one in place, so a caller can
    never accidentally lose track of which route object a prior decision
    (approval, context assembly, verification tier) was actually made
    against.

    initial_risk_class is set once, at first classification, and never
    changes. current_risk_class is the most recent recomputation.
    max_observed_risk_class is the running maximum across every
    recomputation this run has done - the only value anything downstream is
    meant to reason from (see escalate_risk's own docstring)."""

    kind: ChangeKind
    impact: ImpactVector

    initial_risk_class: RiskClass
    current_risk_class: RiskClass
    max_observed_risk_class: RiskClass

    execution_weight: ExecutionWeight

    deterministic_signals: Dict[str, Any] = field(default_factory=dict)
    reason_codes: List[str] = field(default_factory=list)

    router_used: bool = False
    router_confidence: Optional[float] = None

    # MA2.7 - set ONCE at first classification (classify()) and never
    # changed thereafter, same "set once, never changes" contract as
    # initial_risk_class above - what MA2.10's telemetry shape calls
    # impact_initial/initial_execution_weight, kept as real fields
    # (mirroring initial_risk_class's own precedent) rather than derived
    # after the fact, so no caller can construct a route missing this
    # history. Optional/defaulted only so a construction site that
    # predates MA2.7 (an existing test fixture, say) doesn't hard-fail -
    # to_dict() below falls back to impact/execution_weight when unset,
    # which is exactly correct for a route that has never been recomputed.
    initial_impact: Optional[ImpactVector] = None
    initial_execution_weight: Optional[ExecutionWeight] = None

    def with_recomputed_risk(
        self,
        risk: "RiskClass",
        impact: "ImpactVector",
        reason_codes: List[str],
    ) -> "EngineeringRoute":
        """MA2.2 - the one place the monotonic max_observed_risk_class
        invariant is enforced when a FRESH ImpactVector is available (not
        just a bare risk value) - MA2.4's post-Architect recomputation is
        the first real caller: Architect's real touched-file list produces
        a genuinely new ImpactVector, not just a new risk number, and that
        new impact is worth keeping (e.g. for telemetry's `impact_final`)
        rather than discarded once risk is derived from it.

        Returns a NEW EngineeringRoute (this object is frozen) with:
          - impact REPLACED by the new, more complete ImpactVector
          - current_risk_class set to `risk`
          - max_observed_risk_class = max(previous max, risk) - can only
            move up, exactly like escalate_risk's own invariant (this
            method is now that invariant's one real implementation -
            escalate_risk below delegates here)
          - execution_weight recomputed from (kind, the NEW max) - never
            from current_risk_class alone, so a later, narrower
            recomputation can never quietly undo an earlier escalation's
            process weight
          - reason_codes EXTENDED (never replaced), so the full audit
            trail across every recomputation survives, not just the latest
            one

        kind, initial_risk_class, initial_impact, initial_execution_weight,
        deterministic_signals, router_used, and router_confidence are
        carried over unchanged - only the fields listed above ever move
        after first classification."""
        new_max = max(self.max_observed_risk_class, risk)
        return EngineeringRoute(
            kind=self.kind,
            impact=impact,
            initial_risk_class=self.initial_risk_class,
            current_risk_class=risk,
            max_observed_risk_class=new_max,
            execution_weight=determine_execution_weight(self.kind, new_max),
            deterministic_signals=self.deterministic_signals,
            reason_codes=[*self.reason_codes, *reason_codes],
            router_used=self.router_used,
            router_confidence=self.router_confidence,
            initial_impact=self.initial_impact,
            initial_execution_weight=self.initial_execution_weight,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Content-free operational telemetry shape (MA1.4, extended MA2.7) -
        matches GenerationState.generation_metrics()'s own "safe to persist
        in local traces" convention: only the classification and why it
        fired, never goal text or file content. Enum members serialize to
        their plain string/name form so this round-trips cleanly through
        json.dumps (kriya/core/trace.py::TraceLogger.log_run).

        MA2.7 adds the initial-vs-final view that actually makes an
        escalation legible without cross-referencing log lines:
        initial_execution_weight/impact_initial (falling back to the
        current values for a route that predates MA2.7 or was never
        recomputed - identical to current for such a route anyway, so the
        fallback is exactly correct, not a guess), `escalated` (a simple
        risk-class comparison), and `escalation_stage` - "post_architect"
        when the evidence is a reason code MA2.4's recompute_from_files
        actually tagged that way, "none" when nothing escalated,
        "unspecified" for an escalation with no traceable stage tag (e.g.
        a bare escalate_risk() call outside the post-Architect path)."""
        escalated = self.max_observed_risk_class > self.initial_risk_class
        if any(c.startswith("post_architect:") for c in self.reason_codes):
            escalation_stage = "post_architect"
        elif escalated:
            escalation_stage = "unspecified"
        else:
            escalation_stage = "none"
        return {
            "kind": self.kind.value,
            "initial_risk_class": self.initial_risk_class.name,
            "current_risk_class": self.current_risk_class.name,
            "max_observed_risk_class": self.max_observed_risk_class.name,
            "execution_weight": self.execution_weight.value,
            "initial_execution_weight": (self.initial_execution_weight or self.execution_weight).value,
            "final_execution_weight": self.execution_weight.value,
            "impact": asdict(self.impact),
            "impact_initial": asdict(self.initial_impact or self.impact),
            "impact_final": asdict(self.impact),
            "escalated": escalated,
            "escalation_stage": escalation_stage,
            "reason_codes": list(self.reason_codes),
            "deterministic_signals": dict(self.deterministic_signals),
            "router_used": self.router_used,
            "router_confidence": self.router_confidence,
        }


# kind x risk_class -> execution_weight. `milestone` never lands on `light`
# even at low risk - a milestone-shaped request always gets at least
# `standard` depth, per the design's own process-profile table; every other
# kind scales light/standard/heavy directly with risk_class.
_EXECUTION_WEIGHT_TABLE: Dict[ChangeKind, Dict[RiskClass, ExecutionWeight]] = {
    ChangeKind.TASK: {
        RiskClass.LOW: ExecutionWeight.LIGHT,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
    ChangeKind.ENHANCEMENT: {
        RiskClass.LOW: ExecutionWeight.LIGHT,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
    ChangeKind.REFACTOR: {
        RiskClass.LOW: ExecutionWeight.LIGHT,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
    ChangeKind.MILESTONE: {
        RiskClass.LOW: ExecutionWeight.STANDARD,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
}


def determine_execution_weight(kind: ChangeKind, risk: RiskClass) -> ExecutionWeight:
    """A function of BOTH kind and risk_class, never kind alone - the whole
    point of separating the two axes. A `heavy task` (e.g. a one-line
    expired-JWT auth-bypass fix) gets `heavy` depth applied to `task`
    sequencing; it never silently falls back to "tasks get the light
    treatment" just because it reads like a bug report. Pure, same
    contract as determine_risk_class above."""
    return _EXECUTION_WEIGHT_TABLE[kind][risk]


class ContextDepth(str, Enum):
    """How far context retrieval reaches - not where it starts (that's a
    `kind`/strategy question, handled entirely within triage.py and, later,
    the retrieval code itself - see this module's own guardrail above)."""

    NARROW = "narrow"
    DEPENDENCY_AWARE = "dependency_aware"
    IMPACT_WIDE = "impact_wide"


class VerificationTier(str, Enum):
    """Recorded on every profile regardless of whether Kriya's verification
    machinery can actually act on the distinction yet - see MA2.7's own
    scope note (session history): HEAVY is telemetry-true before it's
    behavior-true, and that gap must stay visible, never silently
    papered over by claiming a tier of rigor that isn't real yet."""

    LIGHT = "light"
    STANDARD = "standard"
    HEAVY = "heavy"


@dataclass(frozen=True)
class ProcessProfile:
    """Immutable - one execution_weight always resolves to exactly the same
    ProcessProfile (process_profile_for is a pure lookup, not a stateful
    decision), so two callers holding the same weight can never disagree
    about what it implies."""

    execution_weight: ExecutionWeight

    context_depth: ContextDepth

    planning_required: bool
    structured_subtasks_required: bool
    impact_revalidation_required: bool
    contract_analysis_required: bool

    full_test_suite_required: bool
    human_review_required: bool
    auto_merge_allowed: bool

    verification_tier: VerificationTier

    def to_dict(self) -> Dict[str, Any]:
        """MA2.6b - content-free telemetry shape, same convention as
        EngineeringRoute.to_dict() (kriya/workflow/triage.py). Always
        includes heavy_extended_checks_not_yet_available, computed here
        rather than left to each call site to remember - "HEAVY records
        which future extended checks are not yet available" (MA2.6b's own
        Definition-of-Done line) is guaranteed by construction, not by
        convention."""
        return {
            "execution_weight": self.execution_weight.value,
            "context_depth": self.context_depth.value,
            "planning_required": self.planning_required,
            "structured_subtasks_required": self.structured_subtasks_required,
            "impact_revalidation_required": self.impact_revalidation_required,
            "contract_analysis_required": self.contract_analysis_required,
            "full_test_suite_required": self.full_test_suite_required,
            "human_review_required": self.human_review_required,
            "auto_merge_allowed": self.auto_merge_allowed,
            "verification_tier": self.verification_tier.value,
            "heavy_extended_checks_not_yet_available": self.verification_tier == VerificationTier.HEAVY,
        }

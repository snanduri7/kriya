"""ProcessProfile - what actually decides how much of Kriya's pipeline runs
for a given generation request. MA2.1 of the control-plane implementation
plan (see kriya/workflow/triage.py's own docstring for MA1; session history
for the full MA1-MA6 sequencing).

MA2.1 scope only: the domain model and one pure resolver,
process_profile_for(execution_weight). No wiring into WorkflowEngine yet
(that starts at MA2.3) - this module has no dependency on workflow.py,
kernel, config, or anything else in this run's plumbing. It depends on
nothing but kriya.workflow.triage.ExecutionWeight.

Architectural guardrail, load-bearing for every later MA2 task: this
resolver takes ONLY execution_weight, never `kind`. Context breadth,
planning rigor, verification depth, approval, and autonomy must all be
decided from ProcessProfile - kind may only ever influence work
SEQUENCING/STRATEGY (which triage.py's decision tree already handles), never
depth. Baking a `kind` branch into this resolver, or into anything that
reads a ProcessProfile downstream, is the exact regression the real design
was built to rule out (see triage.py's own JWT-example commentary) - if a
future task feels like it needs `kind` here, that's a sign the decision
belongs in triage.py's classification instead, not here.

Two fields are deliberately False in every profile below even though the
full design eventually wants them true for STANDARD/HEAVY:
structured_subtasks_required (no subtask executor exists - that's MA6) and
contract_analysis_required (no ContractRegistry exists - that's MA5). A
ProcessProfile must never claim a capability Kriya doesn't actually have yet
- flip these only in the same commit that ships the capability itself, not
before.
"""

from dataclasses import dataclass
from enum import Enum

from kriya.workflow.triage import ExecutionWeight


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


LIGHT_PROFILE = ProcessProfile(
    execution_weight=ExecutionWeight.LIGHT,
    context_depth=ContextDepth.NARROW,
    planning_required=False,
    structured_subtasks_required=False,
    impact_revalidation_required=False,
    contract_analysis_required=False,
    full_test_suite_required=False,
    human_review_required=False,
    auto_merge_allowed=True,
    verification_tier=VerificationTier.LIGHT,
)

STANDARD_PROFILE = ProcessProfile(
    execution_weight=ExecutionWeight.STANDARD,
    context_depth=ContextDepth.DEPENDENCY_AWARE,
    planning_required=True,
    structured_subtasks_required=False,  # MA6 - no subtask executor exists yet
    impact_revalidation_required=True,
    contract_analysis_required=False,  # MA5 - no ContractRegistry exists yet
    full_test_suite_required=True,
    human_review_required=True,
    auto_merge_allowed=False,
    verification_tier=VerificationTier.STANDARD,
)

HEAVY_PROFILE = ProcessProfile(
    execution_weight=ExecutionWeight.HEAVY,
    context_depth=ContextDepth.IMPACT_WIDE,
    planning_required=True,
    structured_subtasks_required=False,  # MA6 - no subtask executor exists yet
    impact_revalidation_required=True,
    contract_analysis_required=False,  # MA5 - no ContractRegistry exists yet
    full_test_suite_required=True,
    human_review_required=True,
    auto_merge_allowed=False,
    verification_tier=VerificationTier.HEAVY,
)

_PROFILES_BY_WEIGHT = {
    ExecutionWeight.LIGHT: LIGHT_PROFILE,
    ExecutionWeight.STANDARD: STANDARD_PROFILE,
    ExecutionWeight.HEAVY: HEAVY_PROFILE,
}


def process_profile_for(weight: ExecutionWeight) -> ProcessProfile:
    """Pure lookup, no config, no LLM, no filesystem - same contract as
    triage.py's determine_risk_class/determine_execution_weight. Takes
    ONLY execution_weight; see this module's own docstring for why `kind`
    must never be a parameter here."""
    return _PROFILES_BY_WEIGHT[weight]

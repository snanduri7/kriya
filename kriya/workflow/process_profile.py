"""ProcessProfile - what actually decides how much of Kriya's pipeline runs
for a given generation request. MA2.1 of the control-plane implementation
plan (see kriya/workflow/triage.py's own docstring for MA1; session history
for the full MA1-MA6 sequencing).

MA2.1 scope only: the domain model and one pure resolver,
process_profile_for(execution_weight). No wiring into WorkflowEngine yet
(that starts at MA2.3) - this module has no dependency on workflow.py,
kernel, config, or anything else in this run's plumbing. It depends on
nothing but kriya.policy.risk (ExecutionWeight; and, since C2, its own three
types, which moved there).

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

# C2 (full modularization): ContextDepth, VerificationTier and ProcessProfile are owned by kriya/policy/risk.py and re-exported
# here unchanged (the same objects); the profile table and its resolver stay. Removed in P1.
from kriya.policy.risk import ContextDepth as ContextDepth
from kriya.policy.risk import ExecutionWeight as ExecutionWeight
from kriya.policy.risk import ProcessProfile as ProcessProfile
from kriya.policy.risk import VerificationTier as VerificationTier

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

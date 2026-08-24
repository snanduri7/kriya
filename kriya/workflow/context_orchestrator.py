"""ContextOrchestrator - MA5.7 of the control-plane implementation plan.

Assembles a structured, hashable ContextPackage (kriya/workflow/
context_package.py, MA5.6) from the pieces MA1-5 already know how to
produce: MA2's (kind, execution_weight) retrieval strategy, an already-
resolved milestone spec slice, established-file carryover, and
contract/artifact registry summaries.

REUSE BOUNDARY (section 24: "do not replace these components"):
ContextOrchestrator does NOT itself call DependencyGraph, the LSP
integration, or LocalVectorStore.query_hybrid - that hybrid vector+graph
retrieval already lives deeply embedded inside kriya/workflow/workflow.py's
run_generation_workflow() (its own Graph RAG retrieval stage, ~line 1115
at MA5's time of writing), hardened by a long real-incident history MA5's
own "preserve current execution core" constraint exists to protect.
Extracting or duplicating that logic here would be exactly the kind of
deep pipeline rewrite MA5's Out-of-Scope list rules out (no full
WorkflowController, no per-subtask execution). Instead, build() accepts
that pipeline's ALREADY-ASSEMBLED output as `raw_rag_context` (an opaque
string, wrapped into `baseline` rather than itemized as individual
ContextItems, since the existing retrieval doesn't expose per-file
provenance at that boundary today) - real composition of an existing,
untouched component, not a parallel reimplementation. The same pattern
applies to established_file_context, contract_entries, and
artifact_entries: each is resolved by its own real owner (kriya/workflow/
milestones.py's MilestoneRunState, kriya/control/contracts.py's
ContractRegistry, kriya/control/artifacts.py's ArtifactRegistry) and
handed to build() already-resolved, rather than ContextOrchestrator
reaching into those stores itself and duplicating their own lookup logic.

What THIS module genuinely owns, end to end: MA2's own kind x weight
retrieval-strategy rule (section 25, select_context_strategy - pure,
deterministic, fully tested here), and token-budget-aware assembly of
whatever inputs it's given into ContextItems with real provenance/trust
metadata, real content hashes, and an honest omitted[] list (section 27 -
never a silent truncation) when the token budget doesn't fit everything.
Token estimation reuses kriya/workflow/context_budget.py's own
estimate_tokens() rather than a second heuristic.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.agents.contracts import MilestoneV2
from kriya.control.state import ControlState
from kriya.policy.trust import TrustLevel
from kriya.workflow.context_budget import estimate_tokens
from kriya.workflow.context_package import (
    ContextItem,
    ContextPackage,
    build_context_package,
    make_context_item,
    make_omitted_entry,
)
from kriya.workflow.process_profile import ProcessProfile
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight

# A reasonable, project-independent default - real callers (workflow.py
# integration) are expected to pass their own resolved context_window-
# derived budget; this only matters for a caller that doesn't.
DEFAULT_TOKEN_BUDGET = 8000

# established_file_context entries don't cleanly match any of section 26's
# CONTEXT_SOURCE_TYPES (kriya/workflow/context_package.py) - closest is
# carried_forward_acceptance, but that specifically means ACCEPTANCE
# CRITERIA carried forward (matching ContextPackage.carried_forward_criteria),
# not file content. CONTEXT_SOURCE_TYPES's own docstring explicitly allows
# a new source_type string without a dataclass change; this is that case.
SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT = "established_milestone_output"


class ContextStrategy(str, Enum):
    """Section 25's own three worked examples, plus one documented default
    for every (kind, weight) pair not explicitly named there."""

    REPRODUCTION_FIRST_IMPACT_WIDE = "reproduction_first_impact_wide"
    TARGET_MODULE_DIRECT_CONSUMERS = "target_module_direct_consumers"
    MILESTONE_SPEC_AND_CONTRACTS = "milestone_spec_and_contracts"
    DEFAULT_BALANCED = "default_balanced"


def select_context_strategy(kind: ChangeKind, weight: ExecutionWeight) -> ContextStrategy:
    """Pure, deterministic - never asks a model. Section 25's rule made
    literal: `kind` selects WHERE to retrieve (the strategy's shape),
    `execution_weight` selects HOW FAR (folded into the same lookup rather
    than a second independent axis, since the three worked examples each
    name one specific (kind, weight) pair, not kind alone)."""

    if kind == ChangeKind.TASK and weight == ExecutionWeight.HEAVY:
        return ContextStrategy.REPRODUCTION_FIRST_IMPACT_WIDE
    if kind == ChangeKind.REFACTOR and weight == ExecutionWeight.STANDARD:
        return ContextStrategy.TARGET_MODULE_DIRECT_CONSUMERS
    if kind == ChangeKind.MILESTONE and weight == ExecutionWeight.STANDARD:
        return ContextStrategy.MILESTONE_SPEC_AND_CONTRACTS
    return ContextStrategy.DEFAULT_BALANCED


def _milestone_spec_slice(milestone: MilestoneV2) -> Dict[str, Any]:
    return {
        "id": milestone.id,
        "goal": milestone.goal,
        "mode": milestone.mode.value if milestone.mode else None,
        "depends_on": list(milestone.depends_on),
        "provides": [p.name for p in milestone.provides],
        "consumes": list(milestone.consumes),
        "acceptance": [{"id": a.id, "description": a.description} for a in milestone.acceptance],
    }


@dataclass(frozen=True)
class _Candidate:
    path: str
    content: str
    reason: str
    source_type: str
    trust_level: TrustLevel
    score: Optional[float] = None


def _rank_and_trim(candidates: Sequence[_Candidate], token_budget: int) -> Tuple[Tuple[ContextItem, ...], Tuple[Dict[str, Any], ...]]:
    """Never silently truncates (section 27): a candidate that doesn't fit
    the remaining budget goes into the returned omitted list with its
    real estimated token cost and rank, not dropped without a trace.
    Candidates are kept in the order given - the caller is responsible for
    ordering by actual priority before calling this."""

    kept: List[ContextItem] = []
    omitted: List[Dict[str, Any]] = []
    running_total = 0
    for rank, candidate in enumerate(candidates, start=1):
        cost = estimate_tokens(candidate.content)
        if running_total + cost <= token_budget:
            kept.append(make_context_item(
                path=candidate.path, content=candidate.content, reason=candidate.reason,
                source_type=candidate.source_type, trust_level=candidate.trust_level, score=candidate.score,
            ))
            running_total += cost
        else:
            omitted.append(make_omitted_entry(
                path=candidate.path, rank=rank, reason="exceeds context token budget", estimated_tokens=cost,
            ))
    return tuple(kept), tuple(omitted)


class ContextOrchestrator:
    async def build(
        self,
        *,
        request: str,
        route: EngineeringRoute,
        profile: ProcessProfile,
        workspace_path: str,
        milestone: Optional[MilestoneV2],
        control_state: ControlState,
        raw_rag_context: Optional[str] = None,
        established_file_context: Optional[Dict[str, str]] = None,
        contract_entries: Sequence[Dict[str, Any]] = (),
        artifact_entries: Sequence[Dict[str, Any]] = (),
        carried_forward_criteria: Sequence[str] = (),
        conventions: Optional[Dict[str, Any]] = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> ContextPackage:
        """`request` is accepted (per the design doc's own signature) but
        currently only carried into telemetry-shaped fields, never used to
        drive retrieval directly here - see the module docstring's reuse
        boundary: retrieval against the real request text is exactly the
        job the existing, untouched Graph RAG stage in workflow.py already
        does, and its output arrives here as raw_rag_context."""

        strategy = select_context_strategy(route.kind, profile.execution_weight)

        candidates: List[_Candidate] = []
        for path, content in (established_file_context or {}).items():
            candidates.append(_Candidate(
                path=path, content=content,
                reason="established output from an earlier completed milestone",
                source_type=SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT,
                trust_level=TrustLevel.REPOSITORY,
            ))

        baseline: Optional[Dict[str, Any]] = None
        if raw_rag_context:
            baseline = {
                "source": "existing_hybrid_retrieval",
                "estimated_tokens": estimate_tokens(raw_rag_context),
                "rag_context": raw_rag_context,
            }

        spec_slice = _milestone_spec_slice(milestone) if milestone is not None else None

        # Reserve room for baseline/spec_slice before trimming the
        # itemized candidates - an honest budget split, not itemized
        # content silently starving because a huge raw_rag_context ate the
        # whole allowance unaccounted for.
        reserved = estimate_tokens(str(baseline)) if baseline else 0
        reserved += estimate_tokens(str(spec_slice)) if spec_slice else 0
        remaining_budget = max(0, token_budget - reserved)

        relevant_files, omitted = _rank_and_trim(candidates, remaining_budget)

        total_tokens = reserved + sum(estimate_tokens(item.content) for item in relevant_files)

        return build_context_package(
            conventions=conventions or {},
            relevant_files=relevant_files,
            spec_slice=spec_slice,
            carried_forward_criteria=tuple(carried_forward_criteria),
            contract_entries=tuple(contract_entries),
            artifact_entries=tuple(artifact_entries),
            baseline={**baseline, "strategy": strategy.value} if baseline else {"strategy": strategy.value},
            omitted=omitted,
            token_count=total_tokens,
        )

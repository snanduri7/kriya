"""PLAN-EDIT-FEASIBILITY-001: can a planned edit of an existing file be executed
under Kriya's own edit protocol, decided before any unit runs.

Invariant: a plan is never approved with an edit its own executor can already
be shown to be unable to run. The executor's decision (CONTEXT-EDIT-PROTOCOL-001,
kriya/workflow/edit_capability.py) offers a mutation operation only from
(a) the authoritative whole file, which the known-target allocator admits only
when its cost fits the protected room, (b) an exact window around a deterministic
locus, or (c) a structural insertion locus. This module evaluates the same
sources, with the same helpers, from the inputs that exist at plan time.

Three outcomes; only INFEASIBLE may refuse a plan:

- INFEASIBLE: the whole file cannot fit the known-target room of the model the
  unit's first invocation is routed to (the primary: attempt 1 never starts on a
  fallback, and the stop is non-retryable), AND every source
  of an editable region is proven absent: no goal-named member, no code the goal
  quotes that is found in the file, no structural insertion locus, and the unit's
  own retrieval grounds nothing in the file.
- FEASIBLE: capacity does not exclude the edit (the whole file fits the largest
  possible room) or a source positively yields an operation. It is a statement
  that nothing blocks the plan, never a promise about the real room.
- UNKNOWN: a source could still ground an editable region, or an input needed to
  rule it out is unavailable. UNKNOWN never refuses a plan and never reaches the
  Planner; the runtime decision stands unchanged.

Scope: the FIRST Developer invocation of a unit (attempt 1) on the file as it is
in the workspace at plan time. A unit that follows another unit planning the same
file sees different bytes at run time (a member, quoted code or a class the earlier
unit adds), so it is UNKNOWN, decided by the runtime as before. Later attempts add
failure-derived loci that do not exist before execution; they can only add
sources, so a first-invocation stop is final for the unit (the recovery policy
already treats it as non-retryable)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence

from kriya.workflow.context_budget import _reserve_graph_context_budget, allocation_window, estimate_tokens
from kriya.workflow.context_source import member_boundaries_for, member_ids_matching_name
from kriya.workflow.edit_capability import (
    ANCHORED_EDIT,
    FULL_FILE_REPLACEMENT,
    build_edit_capability,
    exact_window_reserve,
    goal_code_fragments,
    locate_fragments,
)
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.insertion_locus import resolve_insertion_locus

logger = logging.getLogger(__name__)

PLANNED_EDIT_INFEASIBLE = "PLANNED_EDIT_INFEASIBLE"


class Feasibility(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ModelCapacity:
    """A model a unit's first Developer call is routed to and the largest
    known-target room a request to it can have."""

    model: str
    room_tokens: int


@dataclass(frozen=True)
class UnitGrounding:
    """What the unit's own retrieval (the one run_generation_workflow performs
    for the unit goal) grounds, by path: member hints, structural localization
    candidates and their declaration loci. ``None`` for a unit means the
    retrieval was not evaluated."""

    paths: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class EditFeasibility:
    subtask_id: str
    path: str
    outcome: Feasibility
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"subtask_id": self.subtask_id, "path": self.path, "outcome": self.outcome.value,
                "reason": self.reason, "evidence": dict(self.evidence)}


def model_capacities(config: Any) -> List[ModelCapacity]:
    """The model the first Developer invocation of a unit is routed to - the
    primary ``config.llm``; attempt 1 never starts on a ``llm_chain`` fallback
    (``_select_developer_fallback`` needs a retry) and
    CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE is non-retryable - with the largest
    known-target room it can give: what the allocator uses with no skills,
    learned reference, design or graph text beside it
    (``_reserve_graph_context_budget`` with no unbounded text), so every real
    request has at most this much."""
    binding = config.llm
    return [ModelCapacity(model=binding.model, room_tokens=_reserve_graph_context_budget(
        allocation_window(config, binding)))]


def _goal_named_member_ids(goal: str, path: str, content: str) -> List[str]:
    """The members of ``path`` the goal names, validated against the file's
    member boundaries exactly like attempt._validated_member_hints."""
    from kriya.workflow.attempt import _goal_named_member_candidates

    candidates = _goal_named_member_candidates(goal, path)
    boundaries = member_boundaries_for(path, content) if candidates else None
    if not boundaries:
        return []
    matched: List[str] = []
    for name in candidates:
        for member_id in member_ids_matching_name(boundaries, name):
            if member_id not in matched:
                matched.append(member_id)
    return matched


def assess_planned_edit(
    *, subtask_id: str, path: str, content: str, unit_goal: str, grounding_goal: str,
    capacities: Sequence[ModelCapacity], grounding: Optional[UnitGrounding], unit_file_count: int = 1,
) -> EditFeasibility:
    """The feasibility of editing the existing file ``path`` (current bytes
    ``content``) in the unit whose goal text is ``unit_goal``."""
    def result(outcome: Feasibility, reason: str, **evidence: Any) -> EditFeasibility:
        return EditFeasibility(subtask_id, path, outcome, reason, evidence)

    if not capacities:
        return result(Feasibility.UNKNOWN, "no model capacity is available to decide")
    required = estimate_tokens(content)
    largest = max(capacities, key=lambda capacity: capacity.room_tokens)
    if required <= largest.room_tokens:
        return result(Feasibility.FEASIBLE, "the whole file fits the largest possible known-target room",
                      required_tokens=required, available_tokens=largest.room_tokens)

    lines = content.splitlines()
    # Sources of an editable region once the whole file cannot be shown.
    named = _goal_named_member_ids(unit_goal, path, content)
    if named:
        return result(Feasibility.UNKNOWN, "a member the goal names could ground an exact member unit",
                      members=named)
    loci = locate_fragments(lines, goal_code_fragments(unit_goal))
    budget_chars = exact_window_reserve(largest.room_tokens) * 4 // max(1, unit_file_count)
    if loci:
        capability = build_edit_capability(path, content, full_file=False, loci=loci, budget_chars=budget_chars)
        if capability.feasible:
            return result(Feasibility.FEASIBLE, "code the goal quotes locates exact edit windows", loci=loci)
        return result(Feasibility.UNKNOWN, "code the goal quotes is located but its windows exceed the budget",
                      loci=loci)
    insertion, _why = resolve_insertion_locus(
        path, content, goal=grounding_goal or unit_goal, revision=content_revision(content))
    if insertion is not None:
        capability = build_edit_capability(
            path, content, full_file=False, loci=[], budget_chars=budget_chars, insertion=insertion)
        if capability.feasible:
            return result(Feasibility.FEASIBLE, "a structural insertion locus is available")
    if grounding is None:
        return result(Feasibility.UNKNOWN, "the unit's own retrieval was not evaluated", needs_grounding=True)
    if path in grounding.paths:
        return result(Feasibility.UNKNOWN, "the unit's own retrieval grounds this file")
    return result(
        Feasibility.INFEASIBLE,
        "the minimum authoritative unit of this file (the whole file; no member, quoted code, insertion locus or "
        "retrieval grounds a smaller one) exceeds the known-target room of the model the unit is routed to",
        file=path, edit_unit="modification of an existing file", edit_protocol=[ANCHORED_EDIT, FULL_FILE_REPLACEMENT],
        required_capacity_tokens=required, available_capacity_tokens=largest.room_tokens,
        models=[{"model": capacity.model, "room_tokens": capacity.room_tokens} for capacity in capacities],
        provenance=["estimate_tokens(current bytes)", "context_budget._reserve_graph_context_budget(allocation_window)",
                    "attempt._goal_named_member_candidates", "edit_capability.goal_code_fragments/locate_fragments",
                    "insertion_locus.resolve_insertion_locus", "graph_retrieval.retrieve_graph_context"],
    )


def grounded_paths(retrieval: Any) -> FrozenSet[str]:
    """Every path the unit's retrieval could turn into a member hint, a locus
    or a localization target (kriya/workflow/graph_retrieval.py result)."""
    paths = set(retrieval.retrieval_member_hints) | set(retrieval.retrieval_symbol_loci)
    paths |= set(retrieval.verified_grounding) | set(retrieval.hypothesis_candidates)
    paths |= {candidate.path for candidate in retrieval.localization}
    return frozenset(paths)


async def collect_unit_grounding(config: Any, workspace_path: str, unit_goal: str) -> Optional[UnitGrounding]:
    """What ``run_generation_workflow`` would ground for ``unit_goal``: the same
    ``retrieve_graph_context`` call with the same limits, run without any model
    call (the CI-6 localization decision only reorders candidates already
    present, so it cannot add a path). None - UNKNOWN for the caller - when the
    result could differ from the unit's own run: a process-profile retrieval
    depth, a degraded semantic leg, or any retrieval failure. No vector index
    means the unit's retrieval is skipped too, which grounds nothing."""
    if config.process_profiles.enabled and config.process_profiles.enforce_context_depth:
        return None
    memory = config.paths.memory
    vector_index_path = os.path.join(memory, "vector_index.db")
    if not os.path.exists(vector_index_path):
        return UnitGrounding()
    from kriya.memory.embedding import configured_client, run_deadline
    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow.context_budget import RetrievalLimits
    from kriya.workflow.graph_retrieval import retrieve_graph_context

    vector_store = LocalVectorStore(vector_index_path)
    try:
        retrieval = await retrieve_graph_context(
            unit_goal, workspace_path,
            embed_client=configured_client(config), vector_store=vector_store,
            dependency_graph_path=os.path.join(memory, "dependency_graph.db"),
            limits=RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30),
            embedding_model=config.embedding.model, deadline=run_deadline(config),
            budget_limit=lambda: _reserve_graph_context_budget(allocation_window(config)),
        )
    except Exception as error:  # a failed retrieval is UNKNOWN, never a rejection
        logger.warning("Edit feasibility: unit retrieval unavailable (%s: %s)", type(error).__name__, error)
        return None
    finally:
        vector_store.close()
    if retrieval.semantic_unavailable:
        return None
    return UnitGrounding(paths=grounded_paths(retrieval))


def infeasible_results(results: Iterable[EditFeasibility]) -> List[EditFeasibility]:
    return [r for r in results if r.outcome is Feasibility.INFEASIBLE]


def repair_error_text(result: EditFeasibility) -> str:
    """The deterministic contradiction handed to the Planner: what Kriya
    knows, nothing about which file to choose."""
    e = result.evidence
    return (
        f"planned edit of {result.path} (subtask {result.subtask_id!r}) cannot be executed under the currently "
        f"authorized edit protocol and model context: the minimum editable unit of this file is "
        f"{e['required_capacity_tokens']} tokens and the largest room the model this unit runs on can give it "
        f"is {e['available_capacity_tokens']} tokens, and nothing in the goal or the repository grounds a smaller "
        "editable region of it"
    )

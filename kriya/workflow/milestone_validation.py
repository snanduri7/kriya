"""Deterministic Milestone Schema v2 validation - no LLM calls anywhere in
this module. Planner output (raw or normalized via
kriya/workflow/milestone_normalization.py) is untrusted until it passes
MilestonePlanValidator.validate() below - see kriya/agents/contracts.py's
MilestoneV2 docstring and this module's own top-of-file MA3 invariant list
in the design doc this implements: "Planner output is untrusted until
deterministically validated."

Additive through MA3.4: nothing calls MilestonePlanValidator yet - MA3.6
wires it into the real MilestonePlannerAgent pipeline, only once MA3.5 has
loosened the planner prompt's current absolute "DO NOT PROPOSE MULTIPLE
BUILD ARTIFACTS" rule. The deterministic replacement (this module) must
exist before that prompt guardrail is loosened, not after."""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from kriya.agents.contracts import MilestoneMode, MilestoneV2
from kriya.workflow.repository_topology import RepositoryTopology

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MilestoneValidationIssue:
    """One deterministic finding - `code` is a stable reason code (see the
    module-level REASON_CODES set below and MA3's own telemetry spec), never
    free text alone, so a caller (MA3.6's bounded re-plan correction, MA3.9's
    telemetry) can branch on it without parsing `message`."""

    code: str
    milestone_id: Optional[str]
    message: str


@dataclass(frozen=True)
class MilestoneValidationResult:
    """`milestones` is the (possibly normalized - see
    MilestonePlanValidator._normalize_extension_dependencies below)
    validated plan, always returned even when `valid` is False, so a caller
    can inspect exactly what was checked. Only USE the plan downstream when
    `valid` is True; a caller that ignores `valid` and proceeds anyway is a
    bug in the caller, not something this dataclass can prevent."""

    valid: bool
    milestones: List[MilestoneV2]
    errors: List[MilestoneValidationIssue]
    warnings: List[MilestoneValidationIssue]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "milestone_count": len(self.milestones),
            "errors": [
                {"code": i.code, "milestone_id": i.milestone_id, "message": i.message}
                for i in self.errors
            ],
            "warnings": [
                {"code": i.code, "milestone_id": i.milestone_id, "message": i.message}
                for i in self.warnings
            ],
        }


# MA3 section 37's telemetry reason-code vocabulary - DUPLICATE_MILESTONE_ID
# and SELF_DEPENDENCY are this module's own additions. UNJUSTIFIED_ENTRYPOINT
# is MA3.4's pre-execution check below (the only physical-boundary signal
# MilestoneV2 actually carries pre-execution: its own `entrypoint` field).
# UNJUSTIFIED_BUILD_BOUNDARY is reserved for MA3.8's POST-execution regression
# check (diffing two detect_repository_topology() calls, before vs after a
# real milestone run finds/generates files) - a milestone plan alone has no
# field naming a new build ROOT/module path, so that check can only run
# against what was actually written to disk, not the plan.
DUPLICATE_MILESTONE_ID = "DUPLICATE_MILESTONE_ID"
SELF_DEPENDENCY = "SELF_DEPENDENCY"
UNKNOWN_DEPENDENCY = "UNKNOWN_DEPENDENCY"
MILESTONE_DAG_CYCLE = "MILESTONE_DAG_CYCLE"
INVALID_EXTENSION = "INVALID_EXTENSION"
UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
AMBIGUOUS_PROVIDER = "AMBIGUOUS_PROVIDER"
EMPTY_ACCEPTANCE = "EMPTY_ACCEPTANCE"
DUPLICATE_ACCEPTANCE_ID = "DUPLICATE_ACCEPTANCE_ID"
EXTENSION_DEPENDENCY_NORMALIZED = "EXTENSION_DEPENDENCY_NORMALIZED"
UNJUSTIFIED_ENTRYPOINT = "UNJUSTIFIED_ENTRYPOINT"
# Reserved, not yet emitted - a milestone plan alone has no field naming a
# new build ROOT/module path, so this can only be checked post-execution
# (diffing two detect_repository_topology() calls against real generated
# files, not against the plan itself - see this module's own top-of-file
# note on why that's a genuinely different code path from validate() here).
UNJUSTIFIED_BUILD_BOUNDARY = "UNJUSTIFIED_BUILD_BOUNDARY"

# Deliberately small and literal (not a general goal-understanding model) -
# same "revisit once a live run produces real data" restraint as
# kriya/agents/contracts.py's MilestoneList size cap. Mirrors the plan's own
# two "explicit new build boundary" examples ("reusable library", "separate
# CLI executable"/"separate application").
_EXPLICIT_MULTI_ARTIFACT_PHRASES = re.compile(
    r"\b("
    r"separate (application|executable|project|module|service|cli|library)"
    r"|independent (application|executable|project|module|service|library)"
    r"|reusable library"
    r"|standalone (cli|application|executable)"
    r"|multiple (applications|executables|modules|projects|services)"
    r")\b",
    re.IGNORECASE,
)


class MilestonePlanValidator:
    """Stateless - safe to construct once and reuse across plans/calls, same
    convention as EngineeringTriageService (kriya/workflow/triage.py)."""

    def validate(
        self,
        milestones: List[MilestoneV2],
        repository_topology: Optional[RepositoryTopology] = None,
        goal_text: str = "",
    ) -> MilestoneValidationResult:
        """repository_topology/goal_text are optional and default to
        "no check" (None/"") - a caller that only wants the MA3.3 DAG/
        capability/acceptance checks (e.g. this module's own existing tests)
        keeps working unchanged; the MA3.4 physical-topology check below only
        runs when a real topology is supplied."""
        errors: List[MilestoneValidationIssue] = []
        warnings: List[MilestoneValidationIssue] = []

        id_counts: Dict[str, int] = {}
        for m in milestones:
            id_counts[m.id] = id_counts.get(m.id, 0) + 1
        duplicate_ids = {mid for mid, count in id_counts.items() if count > 1}
        for mid in sorted(duplicate_ids):
            errors.append(MilestoneValidationIssue(
                DUPLICATE_MILESTONE_ID, mid,
                f"milestone id '{mid}' is used by {id_counts[mid]} milestones - ids must be unique",
            ))
        # Downstream checks key off id -> milestone; a duplicate id makes that
        # mapping ambiguous, so treat known-ids as this de-duplicated (first
        # occurrence wins) set. The plan stays invalid regardless (the error
        # above already guarantees that) - this only keeps the REST of
        # validation from raising on an ambiguous lookup.
        known_ids: Set[str] = set(id_counts.keys())

        effective_depends_on, normalized_milestones = self._normalize_extension_dependencies(
            milestones, known_ids, warnings
        )

        for m in normalized_milestones:
            deps = effective_depends_on[m.id]
            if m.id in deps:
                errors.append(MilestoneValidationIssue(
                    SELF_DEPENDENCY, m.id, f"milestone '{m.id}' depends on itself",
                ))
            for dep in deps:
                if dep not in known_ids:
                    errors.append(MilestoneValidationIssue(
                        UNKNOWN_DEPENDENCY, m.id,
                        f"milestone '{m.id}' depends on unknown milestone '{dep}'",
                    ))

        cycle = self._find_cycle(effective_depends_on, known_ids)
        if cycle:
            errors.append(MilestoneValidationIssue(
                MILESTONE_DAG_CYCLE, cycle[0],
                "milestone dependency cycle: " + " -> ".join(cycle),
            ))

        for m in normalized_milestones:
            if m.mode == MilestoneMode.EXTENSION:
                if not m.extends:
                    errors.append(MilestoneValidationIssue(
                        INVALID_EXTENSION, m.id,
                        f"milestone '{m.id}' has mode=extension but no 'extends' target",
                    ))
                elif m.extends not in known_ids:
                    errors.append(MilestoneValidationIssue(
                        INVALID_EXTENSION, m.id,
                        f"milestone '{m.id}' extends unknown milestone '{m.extends}'",
                    ))

        provider_map = self._capability_providers(normalized_milestones)
        for m in normalized_milestones:
            if not m.consumes:
                continue
            ancestors = self._ancestors(m.id, effective_depends_on)
            for capability in m.consumes:
                providers = provider_map.get(capability, set())
                reachable_providers = providers & ancestors
                if not reachable_providers:
                    errors.append(MilestoneValidationIssue(
                        UNKNOWN_PROVIDER, m.id,
                        f"milestone '{m.id}' consumes '{capability}', which no reachable "
                        "upstream milestone (via depends_on) provides",
                    ))
                elif len(reachable_providers) > 1:
                    errors.append(MilestoneValidationIssue(
                        AMBIGUOUS_PROVIDER, m.id,
                        f"milestone '{m.id}' consumes '{capability}', which has multiple reachable "
                        f"providers {sorted(reachable_providers)!r}; capability providers must be unique",
                    ))

        for m in normalized_milestones:
            if not m.acceptance:
                errors.append(MilestoneValidationIssue(
                    EMPTY_ACCEPTANCE, m.id,
                    f"milestone '{m.id}' has no acceptance criteria",
                ))

        # Checked globally, not just within one milestone's own list - these
        # ids are used as flat labels wherever acceptance criteria are
        # rendered (goal text, telemetry), so a cross-milestone collision is
        # exactly as ambiguous as a within-milestone one.
        acceptance_id_owners: Dict[str, str] = {}
        for m in normalized_milestones:
            for a in m.acceptance:
                if a.id in acceptance_id_owners:
                    errors.append(MilestoneValidationIssue(
                        DUPLICATE_ACCEPTANCE_ID, m.id,
                        f"acceptance id '{a.id}' is used by both milestone "
                        f"'{acceptance_id_owners[a.id]}' and milestone '{m.id}' - "
                        "acceptance ids must be unique across the whole plan",
                    ))
                else:
                    acceptance_id_owners[a.id] = m.id

        errors.extend(self._check_physical_topology(normalized_milestones, repository_topology, goal_text))

        return MilestoneValidationResult(
            valid=not errors,
            milestones=normalized_milestones,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _normalize_extension_dependencies(
        milestones: List[MilestoneV2], known_ids: Set[str], warnings: List[MilestoneValidationIssue]
    ) -> "tuple[Dict[str, List[str]], List[MilestoneV2]]":
        """Section 11's "extension dependency consistency" rule: `extends: M1`
        without `M1` in `depends_on` is auto-normalized (M1 appended) rather
        than rejected, with a warning recorded - this only fires when the
        extends target is itself a KNOWN id; an unknown extends target is
        left alone here and reported separately as INVALID_EXTENSION,
        avoiding a confusing double report (INVALID_EXTENSION AND
        UNKNOWN_DEPENDENCY for the same root cause)."""
        effective: Dict[str, List[str]] = {}
        normalized: List[MilestoneV2] = []
        for m in milestones:
            deps = list(m.depends_on)
            if m.extends and m.extends in known_ids and m.extends not in deps:
                deps.append(m.extends)
                warnings.append(MilestoneValidationIssue(
                    EXTENSION_DEPENDENCY_NORMALIZED, m.id,
                    f"milestone '{m.id}' extends '{m.extends}' but did not list it in "
                    "depends_on - added automatically",
                ))
                normalized.append(m.model_copy(update={"depends_on": deps}))
            else:
                normalized.append(m)
            effective[m.id] = deps
        return effective, normalized

    @staticmethod
    def _find_cycle(effective_depends_on: Dict[str, List[str]], known_ids: Set[str]) -> Optional[List[str]]:
        """Plain DFS cycle detection restricted to known ids only (an unknown
        dependency edge is already reported separately as UNKNOWN_DEPENDENCY
        and must not make this walk crash or false-report a cycle through a
        node that doesn't exist). Returns the cycle as an id path
        (last element repeats the first) for a readable error message, or
        None if the graph is acyclic."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {mid: WHITE for mid in known_ids}
        path: List[str] = []

        def visit(node: str) -> Optional[List[str]]:
            color[node] = GRAY
            path.append(node)
            for dep in effective_depends_on.get(node, []):
                if dep not in known_ids:
                    continue
                if color[dep] == GRAY:
                    cycle_start = path.index(dep)
                    return path[cycle_start:] + [dep]
                if color[dep] == WHITE:
                    found = visit(dep)
                    if found:
                        return found
            path.pop()
            color[node] = BLACK
            return None

        for mid in sorted(known_ids):
            if color[mid] == WHITE:
                found = visit(mid)
                if found:
                    return found
        return None

    @staticmethod
    def _ancestors(milestone_id: str, effective_depends_on: Dict[str, List[str]]) -> Set[str]:
        """All milestones transitively reachable by walking `depends_on`
        backward from `milestone_id` - i.e. everything that must run before
        it. Visited-set bounded, so a cycle elsewhere in the graph (already
        reported separately) can never make this loop forever."""
        seen: Set[str] = set()
        frontier = list(effective_depends_on.get(milestone_id, []))
        while frontier:
            node = frontier.pop()
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(effective_depends_on.get(node, []))
        return seen

    @staticmethod
    def _capability_providers(milestones: List[MilestoneV2]) -> Dict[str, Set[str]]:
        providers: Dict[str, Set[str]] = {}
        for m in milestones:
            for cap in m.provides:
                providers.setdefault(cap.name, set()).add(m.id)
        return providers

    @staticmethod
    def _check_physical_topology(
        milestones: List[MilestoneV2],
        repository_topology: Optional[RepositoryTopology],
        goal_text: str,
    ) -> List[MilestoneValidationIssue]:
        """MA3.4's physical-topology-preservation rule: "a milestone plan may
        not introduce a new physical build boundary unless either the user
        goal explicitly requires one or existing repository architecture
        provides evidence for it." The only pre-execution signal MilestoneV2
        actually carries for this is its own `entrypoint` field - later
        milestones naming a DIFFERENT entrypoint than the first one the plan
        established is exactly the "competing entry-point classes" shape of
        the real historical incident this rule exists to catch (see
        kriya/agents/agent.py's MilestonePlannerAgent system_prompt).

        Skipped entirely (no issues, ever) when no repository_topology is
        supplied - a caller not ready to provide one (e.g. this module's own
        MA3.3-era tests) gets the old DAG/capability/acceptance-only
        behavior, never a surprise new failure mode."""
        if repository_topology is None:
            return []
        if repository_topology.is_multi_module or len(repository_topology.entrypoints) > 1:
            return []  # existing repo architecture already justifies more than one
        if _EXPLICIT_MULTI_ARTIFACT_PHRASES.search(goal_text or ""):
            return []  # user goal explicitly requires it

        issues: List[MilestoneValidationIssue] = []
        first_entrypoint: Optional[str] = None
        for m in milestones:
            if not m.entrypoint:
                continue
            if first_entrypoint is None:
                first_entrypoint = m.entrypoint
                continue
            if m.entrypoint != first_entrypoint:
                issues.append(MilestoneValidationIssue(
                    UNJUSTIFIED_ENTRYPOINT, m.id,
                    f"milestone '{m.id}' introduces entrypoint '{m.entrypoint}', competing "
                    f"with the plan's own established entrypoint '{first_entrypoint}' - the "
                    "repository is single-module/single-entrypoint and neither the goal text "
                    "nor existing repository architecture justifies a new one; a later "
                    "milestone must extend the existing entrypoint, not create a competing one",
                ))
        return issues


def topological_order(milestones: List[MilestoneV2]) -> List[MilestoneV2]:
    """Deterministic Kahn's-algorithm sort over `depends_on` - MA3 section
    23's own "no parallel execution yet, get the logical model right first"
    rule: kriya/workflow/milestones.py's run_milestones() calls this once per
    run and executes the result strictly in order, never concurrently.

    Assumes the plan is ALREADY validated (acyclic, every dependency
    resolvable, extends already merged into depends_on - see
    MilestonePlanValidator.validate()'s own `milestones` result above, which
    is what a real caller passes here) - this function does not re-validate,
    it only orders; an unresolvable dependency is silently ignored rather
    than raising, so it stays safe to call defensively.

    Ties (multiple milestones whose dependencies are all already satisfied)
    break by ORIGINAL list position, so a plan with only one valid order -
    every legacy-normalized plan, and any hand-edited plan that already lists
    milestones in dependency order - comes back completely unchanged."""
    by_id = {m.id: m for m in milestones}
    order_index = {m.id: i for i, m in enumerate(milestones)}

    in_degree: Dict[str, int] = {m.id: 0 for m in milestones}
    dependents: Dict[str, List[str]] = {m.id: [] for m in milestones}
    for m in milestones:
        for dep in m.depends_on:
            if dep in by_id:
                in_degree[m.id] += 1
                dependents[dep].append(m.id)

    ready = sorted((mid for mid, deg in in_degree.items() if deg == 0), key=lambda mid: order_index[mid])
    result: List[MilestoneV2] = []
    while ready:
        ready.sort(key=lambda mid: order_index[mid])
        current = ready.pop(0)
        result.append(by_id[current])
        for nxt in dependents[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                ready.append(nxt)

    return result


def plan_structure_telemetry(milestones: List[MilestoneV2]) -> Dict[str, int]:
    """MA3.9's structural telemetry fields (design doc section 37) - pure,
    derived straight from the milestone list itself. Deliberately excludes
    validation_attempts/validation_failures/repository_topology/schema_version
    - those belong to the CALLER's own bookkeeping (kriya/workflow/
    milestones.py's plan_milestones(), which knows how many attempts it took
    and what topology it validated against), not to a fact derivable from
    the milestones alone. Safe to call on an INVALID (rejected) plan too -
    structure is still meaningful even when a specific check failed."""
    return {
        "milestone_count": len(milestones),
        "dependency_edges": sum(len(m.depends_on) for m in milestones),
        "extension_count": sum(1 for m in milestones if m.mode == MilestoneMode.EXTENSION),
        "composition_count": sum(1 for m in milestones if m.mode == MilestoneMode.COMPOSITION),
    }

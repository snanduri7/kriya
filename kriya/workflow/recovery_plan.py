"""General Obligation-Centric Recovery Execution - data structures only.

Generalizes MA8.1's owner-recovery loop (kriya/workflow/workflow_controller.py)
from exactly-one-owner to N independently, unambiguously resolved owners. See
`Kriya_General_Obligation_Centric_Recovery_Execution_Implementation_Specification.md`
(repo root) for the full design - this module holds only the plain, dependency-
free dataclasses/enums that spec describes; the functions that DERIVE these
objects (`derive_recovery_participants`, `_order_recovery_groups`,
`build_recovery_execution_plan`) live in workflow_controller.py itself, not
here, because they need `resolve_effective_artifact_owner`/
`_transitive_upstream_ids` - both already defined there. Keeping this module a
pure leaf (no import of workflow_controller.py) avoids a circular import; the
alternative (moving those resolver functions here) would have meant relocating
already-correct, already-tested code for no behavioral reason - see the spec's
own §2/§3 note on this deliberate module-boundary adjustment.

Not a new MA number - this is MA8.1's own owner-recovery mechanism, completed.
It reuses MA9's (kriya/workflow/repair_contract.py) grouping VOCABULARY
(`RepairGroup`'s shape, FileRole-priority ordering) but not its in-memory
candidate-view EXECUTOR - `_invoke_bounded_subtask` already gives cross-owner
groups free candidate visibility via the one shared, persistent
`plan_workspace_path` git worktree, so no `candidate_view`/Rule-2A
materialization is needed at this layer. Names (`RecoveryParticipant`,
`RecoveryOwnerGroup`, `RecoveryExecutionPlan`) are deliberately distinct from
MA9's `RepairContract`/`RepairGroup` - a different layer, not a rename."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class RecoveryParticipantRole(str, Enum):
    """Purely descriptive. A REQUIRED_MUTATION participant has a non-empty
    `mutation_reason` and a uniquely resolved `effective_owner_subtask_id` -
    it is scheduled into a `RecoveryOwnerGroup`. A READ_ONLY_CONTEXT
    participant is named by the raw scope_conflict but excluded from every
    group - visible here for observability/tests, never write-authorized,
    never passed to `_invoke_bounded_subtask`."""

    REQUIRED_MUTATION = "required_mutation"
    READ_ONLY_CONTEXT = "read_only_context"


@dataclass(frozen=True)
class RecoveryParticipant:
    """One artifact considered for one recovery cycle.

    mutation_reason / evidence_ids (v1 scope, spec §6): NOT yet independently
    derived per artifact - both are populated from the SAME aggregate
    scope_conflict['reason']/['raw_evidence'] every required_files entry
    already shares today (retry_strategy.py's own scope-conflict construction
    has no per-file evidence breakdown to draw from). This makes an
    ALREADY-EXISTING aggregate evidence bar explicit and inspectable per
    artifact; it does not add new per-artifact discrimination power in v1.
    `evidence_ids` stays empty, reserved for a future per-artifact evidence
    identifier - built only if a live incident demonstrates the aggregate bar
    admits a wrong participant (this codebase's own standing "add only when a
    live incident demonstrates the need" discipline, see ObligationKind's own
    docstring)."""

    artifact: str
    role: RecoveryParticipantRole
    effective_owner_subtask_id: Optional[str]
    owner_resolution_basis: str  # ArtifactOwnerResolutionBasis.value, or "unresolved"
    mutation_reason: str
    evidence_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryOwnerGroup:
    """One owner subtask and every participant it must satisfy in this
    cycle - the unit `_invoke_bounded_subtask` is actually called against.
    Grouped BY OWNER (not by FileRole, unlike MA9's RepairGroup) since the
    scheduling unit here is a whole subtask invocation: an owner asked to fix
    two of its own files still gets exactly ONE RecoveryOwnerGroup with two
    participants - that owner's own nested run_generation_workflow call is
    already free to touch both files in one pass (its own planned_files
    already allow that), no further per-file grouping is needed within one
    owner's own group.

    depends_on_group_ids: real plan `depends_on` edges between the two
    owners' subtasks are tried FIRST (stronger evidence than a generic role
    default - see relationship_basis); FileRole priority of each group's own
    participant set is the tie-break/fallback when no direct-or-transitive
    plan-dependency edge exists between the owners. Linear chain in v1,
    matching MA9's own RepairGroup v1 precedent - no partial/parallel
    ordering support until real evidence demands it."""

    group_id: str  # f"group.{owner_subtask_id}"
    owner_subtask_id: str
    participants: Tuple[RecoveryParticipant, ...]
    depends_on_group_ids: Tuple[str, ...]
    relationship_basis: str  # "plan_dependency" | "file_role_priority"


class RecoveryExecutionPlanStatus(str, Enum):
    ACTIVE = "active"
    SATISFIED = "satisfied"  # every group locally accepted AND acceptance signal confirmed
    REJECTED = "rejected"    # a group failed locally, or aggregate acceptance failed
    ABANDONED = "abandoned"  # retry/environment budget exhausted while still ACTIVE


@dataclass
class RecoveryExecutionPlan:
    """One full cross-owner recovery cycle for one originating scope_conflict.

    Lives ONLY on `_run_structured_enforce`'s own owner-recovery loop stack
    frame - NOT on GenerationState (that dataclass is per-subtask-ATTEMPT
    scoped; this object spans multiple subtasks' own GenerationState
    instances by construction, since each group's owner gets its own fresh
    nested `run_generation_workflow` call and therefore its own fresh
    GenerationState). No new persistence: the same in-memory-per-cycle
    posture `recovery_generation_by_key`/`recovery_candidate_fingerprints`
    already have (workflow_controller.py) - see the implementation spec §13."""

    id: str  # f"recovery.{originating_subtask_id}.{plan_generation}"
    originating_subtask_id: str
    scope_conflict: Dict[str, Any]
    participants: Tuple[RecoveryParticipant, ...]  # ALL named artifacts, incl. excluded ones
    groups: Tuple[RecoveryOwnerGroup, ...]           # only REQUIRED_MUTATION participants
    group_order: Tuple[str, ...]                     # flattened dependency-respecting group_id order
    status: RecoveryExecutionPlanStatus = RecoveryExecutionPlanStatus.ACTIVE
    active_group_id: Optional[str] = None
    completed_group_ids: Tuple[str, ...] = field(default_factory=tuple)

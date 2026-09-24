"""PRD-008A: the canonical execution model shared by every generation mode.

    User intent -> ExecutionPlan -> WorkUnit[1..N] -> one common executor

A direct `kriya generate` is a plan with exactly one WorkUnit; a milestone
sequence is a plan with N dependency-aware WorkUnits (plus its integration
unit). `source_kind` records where a plan came from and nothing else - it
must never select a different safety implementation.

This module is contracts only: immutable definitions, validation, stable
ordering, a deterministic fingerprint and serialization. Runtime lifecycle
(WorkUnitStatus/WorkUnitState) is deliberately a separate type that never
enters any hash. Nothing here decides resume or completion reuse - PRD-008's
existing validators stay the only authority; these types only carry the
identity fields those validators read.

Identity, deliberately layered (see handover/PRD-008A_CODING_HANDOVER.md):
- ``ExecutionPlan.fingerprint`` - the whole definition, declared order
  included. Provenance, audit and plan-revision detection; NOT a reuse key
  (adding an unrelated unit must not invalidate an unaffected one - the
  PRD-008 S4b rule).
- ``WorkUnit.definition_digest`` - supplied by the adapter from the SOURCE
  definition (a milestone's is ``milestone_definition_digest``), so every
  checkpoint and completion proof written before PRD-008A still matches.
- ``ExecutionPlan.ancestors(unit_id)`` - the upstream closure a reuse
  decision must also hold stable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

EXECUTION_PLAN_SCHEMA_VERSION = 1

# Validation reason codes (machine-readable, stable).
EMPTY_PLAN = "EMPTY_PLAN"
INVALID_WORK_UNIT = "INVALID_WORK_UNIT"
DUPLICATE_WORK_UNIT_ID = "DUPLICATE_WORK_UNIT_ID"
MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
DIRECT_PLAN_UNIT_COUNT = "DIRECT_PLAN_UNIT_COUNT"
INTEGRATION_UNIT_DEPENDENCIES = "INTEGRATION_UNIT_DEPENDENCIES"
UNSUPPORTED_COMMIT_STRATEGY = "UNSUPPORTED_COMMIT_STRATEGY"
PLAN_FINGERPRINT_MISMATCH = "PLAN_FINGERPRINT_MISMATCH"
UNSUPPORTED_PLAN_SCHEMA = "UNSUPPORTED_PLAN_SCHEMA"


class PlanSourceKind(str, Enum):
    """Provenance only - never a switch between safety implementations."""

    DIRECT = "direct"
    MILESTONE = "milestone"
    STRUCTURED = "structured"


class CommitStrategy(str, Enum):
    # Each verified WorkUnit may commit on its own (today's milestone
    # semantics; a one-unit direct plan is the degenerate case).
    INCREMENTAL_WORK_UNIT = "incremental_work_unit"
    # Declared, not implemented: no multi-unit transaction exists in the
    # commit machinery (PRD-004/005), so a plan asking for it fails closed.
    ATOMIC_PLAN = "atomic_plan"


SUPPORTED_COMMIT_STRATEGIES = frozenset({CommitStrategy.INCREMENTAL_WORK_UNIT})


class WorkUnitRole(str, Enum):
    # Ordinary unit of requested work (the direct goal, one milestone).
    PRIMARY = "primary"
    # The milestone sequence's final integration pass. It runs the same
    # generation primitive, commits, and has its own checkpoint identity, so
    # it is a WorkUnit - never an invisible extra execution path. It must
    # depend on every PRIMARY unit.
    INTEGRATION = "integration"


class TerminalPhase(str, Enum):
    """Plan-wide deterministic verification. Runs once every PRIMARY unit is
    VERIFIED and before any INTEGRATION unit (with no integration unit: before
    the plan may be terminal). Not a WorkUnit: it calls no model and writes
    nothing, so it has no checkpoint or commit identity."""

    # run_milestones' replay_prior_milestone_verifications(): re-executes
    # each completed milestone's persisted verification commands before the
    # integration unit runs.
    REPLAY_PRIOR_VERIFICATIONS = "replay_prior_verifications"


class WorkUnitStatus(str, Enum):
    """Runtime lifecycle - never part of a definition or a fingerprint."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _frozen_items(mapping: Optional[Mapping[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    """A mapping as sorted (key, canonical-JSON value) pairs - hashable and
    order-independent, round-trippable through _thaw_items."""
    return tuple(sorted((str(k), _canonical_json(v)) for k, v in (mapping or {}).items()))


def _thaw_items(items: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
    return {key: json.loads(value) for key, value in items}


@dataclass(frozen=True)
class AcceptanceItem:
    id: str
    description: str

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "description": self.description}


@dataclass(frozen=True)
class WorkUnit:
    """The immutable execution definition of one unit of work."""

    id: str
    goal: str
    definition_digest: str
    role: WorkUnitRole = WorkUnitRole.PRIMARY
    acceptance_criteria: Tuple[AcceptanceItem, ...] = ()
    depends_on: Tuple[str, ...] = ()
    provides: Tuple[str, ...] = ()
    consumes: Tuple[str, ...] = ()
    # References into the existing obligation ledger - never obligations
    # themselves (PRD-008A: no second obligation system).
    obligation_refs: Tuple[str, ...] = ()
    verification_requirements: Tuple[str, ...] = ()
    # Source-specific definition detail (milestone mode/extends/entrypoint/
    # adds_dependencies, ...). Part of the definition, so it is fingerprinted.
    provenance: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        # Immutable however it was constructed: sequences become tuples.
        object.__setattr__(self, "role", WorkUnitRole(self.role))
        for name in (
            "acceptance_criteria", "depends_on", "provides", "consumes",
            "obligation_refs", "verification_requirements",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "provenance", tuple(tuple(item) for item in self.provenance))

    @classmethod
    def build(
        cls, *, id: str, goal: str, definition_digest: Optional[str] = None,
        role: WorkUnitRole = WorkUnitRole.PRIMARY,
        acceptance_criteria: Sequence[Tuple[str, str]] = (),
        depends_on: Sequence[str] = (), provides: Sequence[str] = (), consumes: Sequence[str] = (),
        obligation_refs: Sequence[str] = (), verification_requirements: Sequence[str] = (),
        provenance: Optional[Mapping[str, Any]] = None,
    ) -> "WorkUnit":
        """Construct from plain values. ``definition_digest`` defaults to a
        digest of this unit's own definition; an adapter over an existing
        source passes that source's digest instead (backward compatibility
        with persisted checkpoints and completion proofs)."""
        unit = cls(
            id=id, goal=goal, definition_digest="", role=WorkUnitRole(role),
            acceptance_criteria=tuple(AcceptanceItem(cid, text) for cid, text in acceptance_criteria),
            depends_on=tuple(depends_on), provides=tuple(provides), consumes=tuple(consumes),
            obligation_refs=tuple(obligation_refs),
            verification_requirements=tuple(verification_requirements),
            provenance=_frozen_items(provenance),
        )
        digest = definition_digest if definition_digest is not None else _sha256(unit._definition())
        return cls(**{**unit.__dict__, "definition_digest": digest})

    def _definition(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "role": self.role.value,
            "acceptance_criteria": [item.to_dict() for item in self.acceptance_criteria],
            "depends_on": list(self.depends_on),
            "provides": list(self.provides),
            "consumes": list(self.consumes),
            "obligation_refs": list(self.obligation_refs),
            "verification_requirements": list(self.verification_requirements),
            "provenance": _thaw_items(self.provenance),
        }

    def provenance_dict(self) -> Dict[str, Any]:
        return _thaw_items(self.provenance)

    def to_dict(self) -> Dict[str, Any]:
        return {**self._definition(), "definition_digest": self.definition_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkUnit":
        return cls.build(
            id=data["id"], goal=data["goal"], definition_digest=data["definition_digest"],
            role=WorkUnitRole(data.get("role", WorkUnitRole.PRIMARY.value)),
            acceptance_criteria=[(item["id"], item["description"]) for item in data.get("acceptance_criteria", [])],
            depends_on=data.get("depends_on", []), provides=data.get("provides", []),
            consumes=data.get("consumes", []), obligation_refs=data.get("obligation_refs", []),
            verification_requirements=data.get("verification_requirements", []),
            provenance=data.get("provenance") or {},
        )


@dataclass(frozen=True)
class PlanIssue:
    code: str
    detail: str
    work_unit_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "work_unit_id": self.work_unit_id}


class InvalidExecutionPlanError(ValueError):
    """An ExecutionPlan that must not execute. Carries every issue found."""

    def __init__(self, issues: Sequence[PlanIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"[{issue.code}] {issue.detail}" for issue in self.issues))

    @property
    def reason_codes(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(issue.code for issue in self.issues))


def _dependency_cycle(units: Sequence[WorkUnit]) -> Optional[List[str]]:
    """One dependency cycle (as a closed id path), or None. Considers only
    dependencies that name a unit in the plan."""
    graph = {unit.id: [dep for dep in unit.depends_on] for unit in units}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {unit_id: WHITE for unit_id in graph}
    stack: List[str] = []

    def visit(node: str) -> Optional[List[str]]:
        color[node] = GREY
        stack.append(node)
        for dep in graph[node]:
            if dep not in graph:
                continue
            if color[dep] == GREY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for unit in units:
        if color[unit.id] == WHITE:
            found = visit(unit.id)
            if found:
                return found
    return None


def validate_plan_definition(
    source_kind: PlanSourceKind, work_units: Sequence[WorkUnit], commit_strategy: CommitStrategy,
) -> Tuple[PlanIssue, ...]:
    issues: List[PlanIssue] = []
    if not work_units:
        return (PlanIssue(EMPTY_PLAN, "an execution plan needs at least one work unit"),)
    if commit_strategy not in SUPPORTED_COMMIT_STRATEGIES:
        issues.append(PlanIssue(
            UNSUPPORTED_COMMIT_STRATEGY,
            f"commit strategy {commit_strategy.value!r} is declared but not implemented; "
            "no multi-unit commit transaction exists",
        ))
    if source_kind is PlanSourceKind.DIRECT and len(work_units) != 1:
        issues.append(PlanIssue(
            DIRECT_PLAN_UNIT_COUNT, f"a direct plan has exactly one work unit, got {len(work_units)}",
        ))

    seen: Dict[str, int] = {}
    for unit in work_units:
        if not unit.id.strip() or not unit.goal.strip() or not unit.definition_digest:
            issues.append(PlanIssue(INVALID_WORK_UNIT, "work unit id, goal and definition digest must be non-blank", unit.id))
        seen[unit.id] = seen.get(unit.id, 0) + 1
    for unit_id, count in seen.items():
        if count > 1:
            issues.append(PlanIssue(DUPLICATE_WORK_UNIT_ID, f"work unit id {unit_id!r} appears {count} times", unit_id))

    ids = set(seen)
    for unit in work_units:
        for dep in unit.depends_on:
            if dep not in ids:
                issues.append(PlanIssue(MISSING_DEPENDENCY, f"{unit.id!r} depends on unknown unit {dep!r}", unit.id))
    if not any(issue.code == DUPLICATE_WORK_UNIT_ID for issue in issues):
        cycle = _dependency_cycle(work_units)
        if cycle:
            issues.append(PlanIssue(DEPENDENCY_CYCLE, "dependency cycle: " + " -> ".join(cycle), cycle[0]))

    primary_ids = {unit.id for unit in work_units if unit.role is WorkUnitRole.PRIMARY}
    for unit in work_units:
        if unit.role is WorkUnitRole.INTEGRATION and not primary_ids <= set(unit.depends_on):
            missing = sorted(primary_ids - set(unit.depends_on))
            issues.append(PlanIssue(
                INTEGRATION_UNIT_DEPENDENCIES,
                f"integration unit {unit.id!r} must depend on every primary unit; missing {missing}",
                unit.id,
            ))
    return tuple(issues)


def stable_topological_order(work_units: Sequence[WorkUnit]) -> Tuple[WorkUnit, ...]:
    """Kahn's algorithm with ties broken by DECLARED position - the same
    rule as kriya/workflow/milestone_validation.py::topological_order, so a
    milestone plan executes in exactly today's order. Expects a validated
    (acyclic, fully-resolved) plan."""
    position = {unit.id: index for index, unit in enumerate(work_units)}
    by_id = {unit.id: unit for unit in work_units}
    in_degree = {unit.id: 0 for unit in work_units}
    dependents: Dict[str, List[str]] = {unit.id: [] for unit in work_units}
    for unit in work_units:
        for dep in unit.depends_on:
            if dep in by_id:
                in_degree[unit.id] += 1
                dependents[dep].append(unit.id)
    ready = [unit_id for unit_id, degree in in_degree.items() if degree == 0]
    ordered: List[WorkUnit] = []
    while ready:
        ready.sort(key=position.__getitem__)
        current = ready.pop(0)
        ordered.append(by_id[current])
        for dependent in dependents[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
    return tuple(ordered)


@dataclass(frozen=True)
class ExecutionPlan:
    """An immutable, validated plan. An invalid one cannot be constructed."""

    plan_id: str
    source_kind: PlanSourceKind
    work_units: Tuple[WorkUnit, ...]
    commit_strategy: CommitStrategy = CommitStrategy.INCREMENTAL_WORK_UNIT
    terminal_phases: Tuple[TerminalPhase, ...] = ()
    # Where the plan came from (plan file path, milestone group id, ...).
    # Not part of the fingerprint: the same definition loaded from a renamed
    # file is the same plan.
    provenance: Tuple[Tuple[str, str], ...] = ()
    fingerprint: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_units", tuple(self.work_units))
        object.__setattr__(self, "source_kind", PlanSourceKind(self.source_kind))
        object.__setattr__(self, "commit_strategy", CommitStrategy(self.commit_strategy))
        object.__setattr__(self, "terminal_phases", tuple(TerminalPhase(p) for p in self.terminal_phases))
        issues = validate_plan_definition(self.source_kind, self.work_units, self.commit_strategy)
        if issues:
            raise InvalidExecutionPlanError(issues)
        object.__setattr__(self, "fingerprint", _sha256(self._definition()))

    @classmethod
    def build(
        cls, *, plan_id: str, source_kind: PlanSourceKind, work_units: Sequence[WorkUnit],
        commit_strategy: CommitStrategy = CommitStrategy.INCREMENTAL_WORK_UNIT,
        terminal_phases: Sequence[TerminalPhase] = (), provenance: Optional[Mapping[str, Any]] = None,
    ) -> "ExecutionPlan":
        return cls(
            plan_id=plan_id, source_kind=source_kind, work_units=tuple(work_units),
            commit_strategy=commit_strategy, terminal_phases=tuple(terminal_phases),
            provenance=_frozen_items(provenance),
        )

    def _definition(self) -> Dict[str, Any]:
        # Declared order is part of the definition: it breaks topological
        # ties, so reordering can change execution order.
        return {
            "schema": EXECUTION_PLAN_SCHEMA_VERSION,
            "source_kind": self.source_kind.value,
            "commit_strategy": self.commit_strategy.value,
            "terminal_phases": [phase.value for phase in self.terminal_phases],
            "work_units": [unit.to_dict() for unit in self.work_units],
        }

    # ---------------------------------------------------------- navigation

    def unit(self, unit_id: str) -> WorkUnit:
        for unit in self.work_units:
            if unit.id == unit_id:
                return unit
        raise KeyError(unit_id)

    def execution_order(self) -> Tuple[WorkUnit, ...]:
        return stable_topological_order(self.work_units)

    def ancestors(self, unit_id: str) -> Tuple[str, ...]:
        """Every unit ``unit_id`` transitively depends on, in execution order."""
        pending, found = list(self.unit(unit_id).depends_on), set()
        while pending:
            current = pending.pop()
            if current not in found:
                found.add(current)
                pending.extend(self.unit(current).depends_on)
        return tuple(unit.id for unit in self.execution_order() if unit.id in found)

    def descendants(self, unit_id: str) -> Tuple[str, ...]:
        """Every unit that transitively depends on ``unit_id``, in execution order."""
        return tuple(unit.id for unit in self.execution_order() if unit_id in self.ancestors(unit.id))

    def provenance_dict(self) -> Dict[str, Any]:
        return _thaw_items(self.provenance)

    def work_unit_identity(self, unit_id: str) -> Dict[str, Any]:
        """The identity a checkpoint or completion record for this unit
        carries (conceptual key: run_id + plan_id + work_unit_id + stage -
        run_id and stage belong to the record itself). Matching on it
        selects a candidate; PRD-008's validators still decide reuse."""
        unit = self.unit(unit_id)
        return {
            "plan_id": self.plan_id,
            "plan_fingerprint": self.fingerprint,
            "source_kind": self.source_kind.value,
            "work_unit_id": unit.id,
            "role": unit.role.value,
            "definition_digest": unit.definition_digest,
            "ancestor_digests": [self.unit(a).definition_digest for a in self.ancestors(unit.id)],
        }

    # ------------------------------------------------------- serialization

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self._definition(),
            "plan_id": self.plan_id,
            "provenance": self.provenance_dict(),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionPlan":
        """Load a persisted plan. A stored fingerprint that no longer
        matches the stored definition is refused, never recomputed over."""
        schema = data.get("schema")
        if schema != EXECUTION_PLAN_SCHEMA_VERSION:
            raise InvalidExecutionPlanError([PlanIssue(
                UNSUPPORTED_PLAN_SCHEMA, f"execution plan schema {schema!r} is not {EXECUTION_PLAN_SCHEMA_VERSION}",
            )])
        plan = cls.build(
            plan_id=data["plan_id"], source_kind=PlanSourceKind(data["source_kind"]),
            work_units=[WorkUnit.from_dict(unit) for unit in data["work_units"]],
            commit_strategy=CommitStrategy(data["commit_strategy"]),
            terminal_phases=[TerminalPhase(p) for p in data.get("terminal_phases", [])],
            provenance=data.get("provenance") or {},
        )
        stored = data.get("fingerprint")
        if stored != plan.fingerprint:
            raise InvalidExecutionPlanError([PlanIssue(
                PLAN_FINGERPRINT_MISMATCH,
                f"stored plan fingerprint {stored!r} does not match its definition ({plan.fingerprint})",
            )])
        return plan


@dataclass(frozen=True)
class WorkUnitState:
    """Runtime lifecycle of one WorkUnit within one run. Kept apart from
    the definition: a status change never changes any plan identity."""

    work_unit_id: str
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    reason_codes: Tuple[str, ...] = ()
    # For BLOCKED: the failed/blocked upstream unit ids that caused it.
    blocked_by: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "work_unit_id": self.work_unit_id,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "blocked_by": list(self.blocked_by),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkUnitState":
        return cls(
            work_unit_id=data["work_unit_id"], status=WorkUnitStatus(data["status"]),
            reason_codes=tuple(data.get("reason_codes", [])), blocked_by=tuple(data.get("blocked_by", [])),
        )

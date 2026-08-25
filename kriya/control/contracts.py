"""ContractRegistry - MA5.2 (models + lifecycle) and MA5.3 (ContractChange +
consumer invalidation) of the control-plane implementation plan.

MA3's kriya/agents/contracts.py::ProvidedCapability is planning-level
INTENT ("milestone M1 says it will provide a ProtocolClient") - a
deterministic validator (MilestonePlanValidator) can check reachability
for it, but nothing enforces its shape or tracks whether it actually
stabilized. ContractRecord is the MA5 successor: a durable,
lifecycle-managed interface a real consumer milestone can be gated against.
See contract_records_from_provided_capabilities() below for the one-way
bridge MA5.7/workflow integration uses to seed the registry from a real
MilestoneV2 plan's provides[] - this module itself has no opinion on how a
record gets registered, only on what happens to it once it exists.

Lifecycle is strictly linear and one-way:

    PROPOSED -> APPROVED -> FROZEN -> IMPLEMENTED

Only an APPROVED contract may become FROZEN; only a FROZEN (or later,
IMPLEMENTED) contract may be treated as a stable interface a consumer can
build against - see require_stable(). A FROZEN contract's shape is NEVER
edited in place: propose_change()/apply_change() is the only path, and
apply_change() always produces a NEW revision (reset to PROPOSED - the new
shape has not itself been approved/frozen yet) rather than mutating the
existing frozen record. Every revision's shape has a stable content_hash,
and prior revisions remain queryable via history_for() - "every frozen
contract revision has a stable hash" is a promise about REVISIONS, not
just the current one.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

Shape = Union[Dict[str, Any], str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_shape_hash(shape: Shape) -> str:
    blob = json.dumps(shape, sort_keys=True, default=str) if isinstance(shape, dict) else str(shape)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ContractState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    FROZEN = "frozen"
    IMPLEMENTED = "implemented"


# The one-way lifecycle edge each transition method enforces - defined once
# so approve()/freeze()/mark_implemented() share a single source of truth
# for "what's the required current state" rather than three independent,
# driftable checks.
_REQUIRED_PRIOR_STATE: Dict[ContractState, ContractState] = {
    ContractState.APPROVED: ContractState.PROPOSED,
    ContractState.FROZEN: ContractState.APPROVED,
    ContractState.IMPLEMENTED: ContractState.FROZEN,
}


class ContractStateError(ValueError):
    """An invalid lifecycle transition was attempted (e.g. freezing a
    PROPOSED contract, or approving one that's already FROZEN)."""


class ContractNotFoundError(KeyError):
    pass


class ContractChangeConflictError(ValueError):
    """apply_change() was given a ContractChange whose old_revision no
    longer matches the contract's current revision - someone else already
    changed it first. Never silently applied on top of a stale base."""


@dataclass(frozen=True)
class ContractRecord:
    """One revision of one contract. Frozen - every lifecycle transition
    and every shape change produces a NEW ContractRecord (see
    ContractRegistry._transition/apply_change), never mutates this one in
    place."""

    id: str
    name: str

    provider_milestone_id: str

    shape: Shape

    state: ContractState

    consumers: Tuple[str, ...] = ()

    revision: str = "v1"
    content_hash: str = ""

    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider_milestone_id": self.provider_milestone_id,
            "shape": self.shape,
            "state": self.state.value,
            "consumers": list(self.consumers),
            "revision": self.revision,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContractRecord":
        return cls(
            id=data["id"],
            name=data["name"],
            provider_milestone_id=data["provider_milestone_id"],
            shape=data["shape"],
            state=ContractState(data["state"]),
            consumers=tuple(data.get("consumers", ())),
            revision=data.get("revision", "v1"),
            content_hash=data.get("content_hash", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
        )


@dataclass(frozen=True)
class ContractChange:
    """A proposed shape change to an existing contract, produced by
    propose_change() and consumed by apply_change() - a pure value, not
    itself persisted (the resulting new ContractRecord IS; the change
    event itself belongs in the DecisionLedger, MA5.4/5.5, not duplicated
    here)."""

    contract_id: str
    old_revision: str
    proposed_shape: Shape

    reason: str

    affected_consumers: Tuple[str, ...] = ()


class ContractRegistry:
    """In-memory registry with explicit save()/load() to
    .kriya/control/contracts.json (kriya/control/persistence.py). Keeps
    the FULL revision history per contract id (not just the latest), so a
    consumer or telemetry caller can always look back at what a given
    revision's frozen shape actually was."""

    def __init__(self) -> None:
        # contract_id -> ordered list of ContractRecord revisions, oldest
        # first. get()/current state always reads the LAST entry.
        self._history: Dict[str, List[ContractRecord]] = {}

    # --- registration ---

    def register(
        self,
        contract_id: str,
        name: str,
        provider_milestone_id: str,
        shape: Shape,
        consumers: Sequence[str] = (),
    ) -> ContractRecord:
        """Raises ValueError if contract_id is already registered - this is
        deliberately NOT idempotent-on-shape-change; a real shape change to
        an existing id must go through propose_change()/apply_change() so
        it's explicit and auditable (section 12), never a second register()
        call quietly overwriting history."""

        if contract_id in self._history:
            raise ValueError(
                f"Contract '{contract_id}' is already registered - use propose_change()/"
                "apply_change() to modify an existing contract's shape, never register() again."
            )
        record = ContractRecord(
            id=contract_id,
            name=name,
            provider_milestone_id=provider_milestone_id,
            shape=shape,
            state=ContractState.PROPOSED,
            consumers=tuple(consumers),
            revision="v1",
            content_hash=compute_shape_hash(shape),
        )
        self._history[contract_id] = [record]
        return record

    # --- lifecycle ---

    def _transition(self, contract_id: str, target_state: ContractState) -> ContractRecord:
        current = self.get(contract_id)
        required_prior = _REQUIRED_PRIOR_STATE[target_state]
        if current.state != required_prior:
            raise ContractStateError(
                f"Cannot move contract '{contract_id}' to {target_state.value} from "
                f"{current.state.value} - requires {required_prior.value} first."
            )
        updated = replace(current, state=target_state, updated_at=_now_iso())
        self._history[contract_id][-1] = updated
        return updated

    def add_consumer(self, contract_id: str, consumer_milestone_id: str) -> ContractRecord:
        """Records that `consumer_milestone_id` depends on this contract -
        real runtime population of ContractRecord.consumers, previously
        only ever settable at register() time and never actually called
        with a non-empty value anywhere (confirmed dead field, 2026-08-25
        external review). Deliberately NOT a lifecycle transition
        (_transition/state unchanged, no revision bump) - who consumes a
        contract is bookkeeping about the contract, not a change to its
        shape or approval state, and must be addable at ANY state
        (a PROPOSED contract can already have known consumers lined up).
        Idempotent - adding the same consumer twice is a no-op, not a
        duplicate entry."""
        current = self.get(contract_id)
        if consumer_milestone_id in current.consumers:
            return current
        updated = replace(
            current, consumers=current.consumers + (consumer_milestone_id,), updated_at=_now_iso(),
        )
        self._history[contract_id][-1] = updated
        return updated

    def approve(self, contract_id: str) -> ContractRecord:
        return self._transition(contract_id, ContractState.APPROVED)

    def freeze(self, contract_id: str) -> ContractRecord:
        return self._transition(contract_id, ContractState.FROZEN)

    def mark_implemented(self, contract_id: str) -> ContractRecord:
        return self._transition(contract_id, ContractState.IMPLEMENTED)

    # --- queries ---

    def get(self, contract_id: str) -> ContractRecord:
        history = self._history.get(contract_id)
        if not history:
            raise ContractNotFoundError(contract_id)
        return history[-1]

    def try_get(self, contract_id: str) -> Optional[ContractRecord]:
        history = self._history.get(contract_id)
        return history[-1] if history else None

    def history_for(self, contract_id: str) -> Tuple[ContractRecord, ...]:
        return tuple(self._history.get(contract_id, ()))

    def list_for_provider(self, provider_milestone_id: str) -> Tuple[ContractRecord, ...]:
        return tuple(
            history[-1] for history in self._history.values()
            if history[-1].provider_milestone_id == provider_milestone_id
        )

    def consumers_of(self, contract_id: str) -> Tuple[str, ...]:
        return self.get(contract_id).consumers

    def require_stable(self, contract_id: str) -> ContractRecord:
        """Raises ContractStateError unless the contract is FROZEN or
        IMPLEMENTED - "only FROZEN contracts may be consumed as stable
        implementation surfaces" (section 11), extended to IMPLEMENTED
        since that's strictly further along the same one-way lifecycle."""

        record = self.get(contract_id)
        if record.state not in (ContractState.FROZEN, ContractState.IMPLEMENTED):
            raise ContractStateError(
                f"Contract '{contract_id}' is {record.state.value}, not FROZEN/IMPLEMENTED - "
                "not yet a stable interface a consumer may build against."
            )
        return record

    def all_records(self) -> Tuple[ContractRecord, ...]:
        return tuple(history[-1] for history in self._history.values())

    # --- change / invalidation (MA5.3) ---

    def propose_change(self, contract_id: str, proposed_shape: Shape, reason: str) -> ContractChange:
        """Pure computation - does not mutate the registry. Identifies the
        CURRENT record's consumers as affected_consumers, per the change
        flow (section 12): propose -> identify consumers -> invalidate
        affected plans (caller's job, via the returned affected_consumers
        and a DecisionLedger record - MA5 does not itself re-plan/re-gate,
        see the package's own Out-of-Scope list) -> re-plan/re-gate later."""

        current = self.get(contract_id)
        return ContractChange(
            contract_id=contract_id,
            old_revision=current.revision,
            proposed_shape=proposed_shape,
            reason=reason,
            affected_consumers=current.consumers,
        )

    def apply_change(self, change: ContractChange) -> ContractRecord:
        """Raises ContractChangeConflictError if change.old_revision no
        longer matches the contract's current revision (someone else
        already applied a different change first). Always produces a NEW
        revision reset to PROPOSED - a changed shape has NOT itself been
        approved/frozen, regardless of what state the prior revision was
        in; it must earn FROZEN again before being trusted as stable.
        consumers carries over unchanged (they're still the contract's
        nominal dependents - it's THEIR job, informed by change.
        affected_consumers, to re-validate against the new shape, not this
        registry's)."""

        current = self.get(change.contract_id)
        if current.revision != change.old_revision:
            raise ContractChangeConflictError(
                f"Contract '{change.contract_id}' is now at revision {current.revision}, "
                f"but this change was proposed against {change.old_revision} - stale, refusing "
                "to silently apply on top of a shape neither side agreed on."
            )
        history = self._history[change.contract_id]
        new_record = ContractRecord(
            id=current.id,
            name=current.name,
            provider_milestone_id=current.provider_milestone_id,
            shape=change.proposed_shape,
            state=ContractState.PROPOSED,
            consumers=current.consumers,
            revision=f"v{len(history) + 1}",
            content_hash=compute_shape_hash(change.proposed_shape),
        )
        history.append(new_record)
        return new_record

    # --- persistence ---

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contracts": {
                contract_id: [record.to_dict() for record in history]
                for contract_id, history in self._history.items()
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContractRegistry":
        registry = cls()
        for contract_id, records in data.get("contracts", {}).items():
            registry._history[contract_id] = [ContractRecord.from_dict(r) for r in records]
        return registry


def register_provided_capabilities(
    registry: "ContractRegistry", milestone: Any,
) -> Tuple[Tuple[ContractRecord, ...], Tuple[ContractChange, ...]]:
    """The one-way bridge this module's own docstring promised since MA5.2
    but was never built (confirmed dead - zero callers anywhere - until
    2026-08-24): registers one PROPOSED ContractRecord per entry in
    `milestone.provides` (kriya/agents/contracts.py's MilestoneV2.provides,
    a List[ProvidedCapability] - MA3 planning-level intent, "milestone M1
    says it will provide X", already real and validated for reachability by
    kriya/workflow/milestone_validation.py's MilestonePlanValidator, but
    never durably tracked past plan-validation time until now).

    `milestone` is typed Any, not MilestoneV2, to avoid this control-plane
    module importing kriya.agents.contracts (a planning-layer module) -
    only `.id`/`.provides` (each with `.name`/`.description`) are actually
    read, structurally.

    contract_id is scoped f"{milestone.id}:{capability.name}", not just
    capability.name - reachability between a provider and a consumer is
    milestone_validation.py's job (a capability NAME is the resolution
    key there); this registry only needs a stable, globally-unique id, and
    two different milestones are free to declare the same capability name
    without colliding here.

    shape is capability.description when given, else capability.name -
    ProvidedCapability has no formal shape/schema field, only a name and
    an optional free-text description; this bridge does not invent a
    schema that was never there. Already-registered ids with an UNCHANGED
    shape are returned as-is, not re-registered (register() would raise) -
    a resumed multi-milestone run re-processing a milestone whose
    capabilities were already registered on an earlier attempt must not
    crash. Already-registered ids whose shape genuinely DIFFERS from what
    was previously recorded (a real, if unusual, case - the same workspace
    later planned with a milestone/capability name that collides with an
    earlier, unrelated goal's) go through propose_change()/apply_change()
    (MA5.3) instead of being silently overwritten or silently ignored -
    2026-08-25, external review: this bridge previously always took the
    "already registered" branch unconditionally, meaning a real shape
    change could never be detected or recorded at all.

    Returns (records, changes) - `changes` is only ever non-empty for a
    genuine shape change just applied in THIS call (never for a first-time
    registration or a no-op re-registration), each carrying the real
    ContractChange.affected_consumers a caller (kriya/workflow/milestones.py
    ::run_milestones(), MA7-C3) can act on - e.g. invalidating a consumer
    milestone that already ran against the now-stale shape."""
    records: List[ContractRecord] = []
    changes: List[ContractChange] = []
    for capability in milestone.provides:
        contract_id = f"{milestone.id}:{capability.name}"
        new_shape = capability.description or capability.name
        existing = registry.try_get(contract_id)
        if existing is None:
            records.append(registry.register(
                contract_id=contract_id,
                name=capability.name,
                provider_milestone_id=milestone.id,
                shape=new_shape,
            ))
        elif existing.shape != new_shape:
            change = registry.propose_change(
                contract_id, proposed_shape=new_shape,
                reason=(
                    f"milestone {milestone.id!r}'s declared shape for capability "
                    f"{capability.name!r} differs from the previously registered shape"
                ),
            )
            records.append(registry.apply_change(change))
            changes.append(change)
        else:
            records.append(existing)
    return tuple(records), tuple(changes)


def contract_records_from_provided_capabilities(
    registry: "ContractRegistry", milestone: Any,
) -> Tuple[ContractRecord, ...]:
    """Thin, backward-compatible wrapper over register_provided_capabilities()
    above, for any caller that only needs the records (not real-time
    change detection) - kept so the pre-2026-08-25 call signature/contract
    (and every test written against it) stays valid unchanged."""
    records, _changes = register_provided_capabilities(registry, milestone)
    return records


def wire_contract_consumers(registry: "ContractRegistry", milestones: Any) -> None:
    """The other half of the P0 gap the 2026-08-25 external review found:
    contract_records_from_provided_capabilities() above registers the
    PROVIDER side of every declared capability, but nothing ever populated
    ContractRecord.consumers from the matching milestone.consumes[] - so
    ContractChange.affected_consumers (propose_change()'s whole reason for
    existing) was always empty, and consumers_of() could never answer "who
    actually depends on this." Call this once, after every milestone in
    `milestones` has already been registered via
    contract_records_from_provided_capabilities() (run_milestones() does
    both in one pass, before the execution loop starts).

    Builds a simple capability-name -> provider-milestone-id map directly
    from `milestones` (NOT re-deriving milestone_validation.py's own
    ancestry-aware reachability check - by the time run_milestones() calls
    this, the plan has ALREADY passed MilestonePlanValidator's reachability
    validation, so every name in some milestone's consumes[] is guaranteed
    to have at least one provider that check already confirmed reachable;
    re-deriving that here would duplicate, not add, safety).

    A capability name with exactly one provider wires normally. A
    capability name provided by MORE THAN ONE milestone is ambiguous - this
    function does not guess which provider a consumer actually meant, so it
    logs a warning and wires NO consumer link for that name at all, rather
    than (as an earlier version of this function did, 2026-08-25 external
    review follow-up) silently attaching the consumer to every candidate
    provider. milestone_validation.py's own reachability check still
    tolerates the same ambiguity at plan-validation time (a real, separate
    design question about whether planning should allow it at all, out of
    scope here) - this function's own job is narrower: never record a
    consumer relationship it isn't actually sure of. A missing provider
    (already-invalid per the plan validator, defensive-only here) is
    likewise skipped, not guessed at.

    `milestones` is typed Any (an Iterable of MilestoneV2-shaped objects),
    matching contract_records_from_provided_capabilities()'s own reasoning
    for not importing kriya.agents.contracts into this control-plane
    module."""
    provider_ids_by_capability: Dict[str, List[str]] = {}
    for m in milestones:
        for capability in m.provides:
            provider_ids_by_capability.setdefault(capability.name, []).append(m.id)

    for m in milestones:
        for capability_name in m.consumes:
            providers = provider_ids_by_capability.get(capability_name, [])
            if len(providers) != 1:
                if len(providers) > 1:
                    logger.warning(
                        f"wire_contract_consumers: capability {capability_name!r} has "
                        f"{len(providers)} providers {sorted(providers)!r} - ambiguous, "
                        f"refusing to guess which one milestone {m.id!r} actually consumes. "
                        "No consumer link recorded for this capability."
                    )
                continue
            contract_id = f"{providers[0]}:{capability_name}"
            if registry.try_get(contract_id) is not None:
                registry.add_consumer(contract_id, m.id)


def mark_capabilities_implemented(registry: "ContractRegistry", milestone: Any) -> None:
    """Called once `milestone` (the PROVIDER) has genuinely completed - real
    Quality Gates passed, real files applied to the real workspace, not a
    planning-time assumption. An autonomous pipeline has no separate
    "a human approved this contract's shape" step distinct from the
    providing milestone finishing for real, so APPROVED -> FROZEN ->
    IMPLEMENTED (contracts.py's own strictly-linear lifecycle) collapse
    into the same real event here - an honest reflection of what this
    pipeline actually has a signal for, not a simulated finer-grained
    review workflow. No-ops per capability (non-fatal, never raises) when
    it was never registered (a plan that changed shape mid-run) or is
    already past PROPOSED (a resumed run re-processing an already-
    IMPLEMENTED milestone)."""
    for capability in milestone.provides:
        contract_id = f"{milestone.id}:{capability.name}"
        record = registry.try_get(contract_id)
        if record is None or record.state != ContractState.PROPOSED:
            continue
        registry.approve(contract_id)
        registry.freeze(contract_id)
        registry.mark_implemented(contract_id)

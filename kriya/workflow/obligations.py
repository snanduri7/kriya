"""MA8: Cross-Revision Obligation Control - a small, generic, per-
workflow-run tracker for a requirement that must hold ACROSS a sequence
of revisions (plan repairs, subtask attempts, gate re-checks), not just
within one isolated check.

Why this exists (PRV-05, 2026-08-28, run #8, after run #7's stage-aware
migration/attribution/self-diagnosis fixes already shipped): MA1-MA7 built
safe, bounded, ownership-respecting EXECUTION of a validated plan - a
different problem from tracking whether a property spanning the WHOLE
plan or repair history stays true across revisions. Run #8 produced two
concrete, live defects from that missing axis, in the SAME run, neither
touched by run #7's fixes:

1. Hardened crashed at plan validation, before any subtask ever ran. The
   Planner fixed refactor_baseline (blank -> "s3") on repair attempt 2,
   but silently REGRESSED an already-fixed planned-file action (s4's
   JsonServiceTest.java, correctly "create" after repair 1, back to the
   wrong "modify" at repair 2) - because each repair prompt only ever
   showed the CURRENT attempt's error list, with no memory that both
   constraints must hold simultaneously. Two repair attempts (the bound)
   exhausted without ever reaching a state where both were true together.
2. Legacy reached attempt 8 with the real migration correctly complete
   (the model's own attempt-3/4 diagnoses had already added jackson-
   datatype-jsr310 and registered JavaTimeModule) - but SpecComplianceAgent
   (pure LLM judgment, zero grounding in the deterministic migration
   state Kriya had already computed) hallucinated "the code still uses
   Jackson... does not show evidence of replacing the old dependency" and
   drove 3 further wasted, destabilizing attempts (NO-OP edit, misdirected
   edit, anchor failure) before the retry budget ran out.

Both trace to the same missing primitive: no shared, authoritative record
of which typed requirements are currently satisfied, and no rule
preventing a judgment-based check from contradicting a deterministic one
that already exists for the same requirement.

Deliberately NOT a universal goal-contract system and NOT event-sourced/
checkpoint-persisted (that's an explicit future extension, not built here)
- a small, per-run, in-memory ledger with exactly two rules:

  - Regression detection: recording a new VIOLATED status for an
    obligation that was previously SATISFIED, from a same-or-higher-
    authority source, is captured as a RegressionEvent. This never blocks
    the transition (the ledger records ground truth, it doesn't gatekeep
    it) - it's surfaced for the caller (the plan-repair loop's MUST-
    PRESERVE section, oscillation detection) to act on.
  - Authority precedence: DETERMINISTIC > GROUNDED > JUDGMENT. A judgment-
    authority verdict must never be treated as overriding a deterministic
    one recorded for the same obligation id.

find_migration_incomplete()'s own stage-aware logic (PRV-05 run #7,
kriya/workflow/migration.py) is UNCHANGED by this module - it becomes the
first MIGRATION_COMPLETION obligation producer feeding this ledger via an
optional parameter, not rewritten. Records use only plain dataclasses/
enums (JSON-serializable) so checkpoint persistence can be added later
without a shape change."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple


class ObligationKind(str, Enum):
    """Deliberately just the three kinds run #8 actually produced evidence
    for. Do not add a new kind speculatively - add one only when a live
    incident, like this one, demonstrates the need (matching this
    codebase's established discipline for every other typed-Failure/
    reason-code addition in this module family)."""

    PLAN_STRUCTURAL_VALIDITY = "plan_structural_validity"
    MIGRATION_COMPLETION = "migration_completion"
    GOAL_SPEC_REQUIREMENT = "goal_spec_requirement"


class ObligationStatus(str, Enum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    PENDING = "pending"
    INDETERMINATE = "indeterminate"


class ObligationAuthority(str, Enum):
    """Precedence order, highest first: DETERMINISTIC > GROUNDED >
    JUDGMENT - see precedence() below. DETERMINISTIC: a structural fact
    computed from parsed/real state (a manifest's own parsed dependency
    list, a plan validator's own structural check). GROUNDED: a
    repository-grounded scan/discovery, real but requiring some
    interpretation (e.g. an architectural-owner scan). JUDGMENT: an LLM's
    own semantic verdict (e.g. SpecComplianceAgent) - real evidence, but
    never authoritative over a DETERMINISTIC or GROUNDED record for the
    same obligation id."""

    DETERMINISTIC = "deterministic"
    GROUNDED = "grounded"
    JUDGMENT = "judgment"

    def precedence(self) -> int:
        """Higher = more authoritative."""
        return {
            ObligationAuthority.DETERMINISTIC: 2,
            ObligationAuthority.GROUNDED: 1,
            ObligationAuthority.JUDGMENT: 0,
        }[self]


@dataclass(frozen=True)
class ObligationRecord:
    """One snapshot of one obligation's state, as of one revision.

    id: stable across revisions - e.g. "plan.refactor_baseline.non_blank",
    "migration.source_dependency_absent". MUST be derived from the
    obligation's own identity (a field name, a manifest path, a migration
    reason code), NEVER from an LLM's own free-text error message - that
    text can be reworded attempt to attempt even when it means the same
    thing, which would silently defeat regression detection and MUST-
    PRESERVE tracking (both keyed by id equality).

    revision: a plain, comparable marker for "when this was recorded" - a
    plan repair_attempts count, a subtask attempt_number, or a subtask id
    string, whatever the producing subsystem's own natural revision axis
    is. Used only for ordering/display, never for equality.

    owner_subtask_id / terminal_required / repair_scope (spec §12, added
    2026-08-28 without a Definition/State split - see this module's own
    docstring for why the split was skipped): kept on the single merged
    record type rather than factored into a separate ObligationDefinition,
    since every producer in this codebase already recomputes/re-records an
    obligation's full state on every call anyway (there's no separately-
    evolving "definition" today that would justify carrying it apart from
    state). owner_subtask_id is the SINGLE subtask that owns satisfying
    this obligation when unambiguous (None when unowned, plan-level, or
    genuinely multi-owned). terminal_required marks an obligation that
    must be SATISFIED for the whole workflow run to be allowed to succeed
    (consumed by ObligationLedger.unresolved_terminal_obligations()).
    repair_scope is the tuple of file paths whose content this specific
    obligation's evidence implicates - i.e. the only files a repair
    targeting THIS obligation may legally touch (empty when the
    obligation isn't file-repairable, e.g. a plan-structural constraint
    fixed via plan repair, not a file edit)."""

    id: str
    kind: ObligationKind
    status: ObligationStatus
    authority: ObligationAuthority
    description: str
    source: str
    revision: Any = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    owner_subtask_id: Optional[str] = None
    terminal_required: bool = False
    repair_scope: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RegressionEvent:
    """A SATISFIED obligation was reported VIOLATED again, by a same-or-
    higher-authority source than the one that satisfied it."""

    obligation_id: str
    kind: ObligationKind
    previous: ObligationRecord
    current: ObligationRecord


class ObligationLedger:
    """One instance per workflow run, threaded through every subsystem
    that produces or consumes an obligation for that run - the plan-repair
    loop, AttemptContext.obligation_ledger (kriya/workflow/attempt.py),
    and SpecCompliance arbitration all read/write the SAME instance so
    "satisfied" means the same thing everywhere in one run."""

    def __init__(self) -> None:
        self._history: Dict[str, List[ObligationRecord]] = {}
        self.regressions: List[RegressionEvent] = []

    def record(self, rec: ObligationRecord) -> Optional[RegressionEvent]:
        """Appends rec to the obligation's history. Returns a
        RegressionEvent when this record downgrades a previously-SATISFIED
        obligation to VIOLATED via a same-or-higher-authority source.
        Never prevents the transition - this ledger records what actually
        happened, it does not gatekeep it (see this module's own
        docstring)."""
        history = self._history.setdefault(rec.id, [])
        regression: Optional[RegressionEvent] = None
        if history:
            previous = history[-1]
            if (
                previous.status == ObligationStatus.SATISFIED
                and rec.status == ObligationStatus.VIOLATED
                and rec.authority.precedence() >= previous.authority.precedence()
            ):
                regression = RegressionEvent(
                    obligation_id=rec.id, kind=rec.kind, previous=previous, current=rec,
                )
                self.regressions.append(regression)
        history.append(rec)
        return regression

    def current(self, obligation_id: str) -> Optional[ObligationRecord]:
        """The obligation's AUTHORITATIVE state (spec §16), never a bare
        "last written" lookup - a lower-authority record arriving after an
        already-established higher-authority one must not become "current"
        (spec §3.3: "an LLM-based evaluator must never invalidate a
        requirement already conclusively satisfied by stronger
        authoritative evidence"). Bug found and fixed 2026-08-28: this used
        to return history[-1] unconditionally - correct for every producer
        in this codebase TODAY (plan_validation.py/migration.py only ever
        record DETERMINISTIC, so every real sequence is single-authority
        and history[-1] happened to already be right) but silently wrong
        the moment any JUDGMENT/GROUNDED producer starts writing to the
        ledger, which this architecture explicitly anticipates (see
        SpecComplianceAgent's authority=JUDGMENT).

        Algorithm: walk history in order, keeping the most recent record
        whose authority is same-or-higher than the authority of the record
        currently held as authoritative. A record with LOWER authority than
        the one already established is skipped entirely - it still exists
        in history() (nothing here hides or discards it), it simply never
        becomes the authoritative state. This is the exact same precedence
        comparison record() already uses for regression detection
        (authority.precedence() >= previous.authority.precedence()) -
        deliberately reusing the identical rule rather than a second,
        driftable one."""
        history = self._history.get(obligation_id)
        if not history:
            return None
        authoritative = history[0]
        for rec in history[1:]:
            if rec.authority.precedence() >= authoritative.authority.precedence():
                authoritative = rec
        return authoritative

    def authoritative_state(self, obligation_id: str) -> Optional[ObligationRecord]:
        """Alias for current() - spec §14 names this explicitly as its own
        method. current() has always meant "authoritative state," never a
        raw last-write lookup (see its own docstring) - a separate,
        non-arbitrated getter is deliberately NOT provided, since that
        would just reintroduce the same footgun under a different name."""
        return self.current(obligation_id)

    def history(self, obligation_id: str) -> List[ObligationRecord]:
        return list(self._history.get(obligation_id, []))

    def ids_by_kind(self, kind: ObligationKind) -> List[str]:
        return [oid for oid, hist in self._history.items() if hist and hist[-1].kind == kind]

    def current_by_kind(self, kind: ObligationKind) -> List[ObligationRecord]:
        return [rec for rec in (self.current(oid) for oid in self.ids_by_kind(kind)) if rec is not None]

    def satisfied_ids(self, kind: Optional[ObligationKind] = None) -> List[str]:
        return [
            oid for oid in self._history
            if (rec := self.current(oid)) is not None
            and rec.status == ObligationStatus.SATISFIED
            and (kind is None or rec.kind == kind)
        ]

    def violated_ids(self, kind: Optional[ObligationKind] = None) -> List[str]:
        return [
            oid for oid in self._history
            if (rec := self.current(oid)) is not None
            and rec.status == ObligationStatus.VIOLATED
            and (kind is None or rec.kind == kind)
        ]

    def detect_oscillation(self, obligation_id: str) -> bool:
        """True when this obligation's own history contains the
        subsequence VIOLATED -> SATISFIED -> VIOLATED (not necessarily
        consecutive records - a repeated re-check landing on the same
        status in between is still the same real oscillation). Callers
        report PLAN_REPAIR_OSCILLATION with this id and its full revision
        history when this fires."""
        statuses = [rec.status for rec in self._history.get(obligation_id, [])]
        seen_violated = False
        seen_violated_then_satisfied = False
        for status in statuses:
            if not seen_violated:
                seen_violated = status == ObligationStatus.VIOLATED
            elif not seen_violated_then_satisfied:
                seen_violated_then_satisfied = status == ObligationStatus.SATISFIED
            elif status == ObligationStatus.VIOLATED:
                return True
        return False

    def oscillating_ids(self, kind: Optional[ObligationKind] = None) -> List[str]:
        return [
            oid for oid in self._history
            if self.detect_oscillation(oid)
            and (kind is None or any(rec.kind == kind for rec in self._history[oid]))
        ]

    def unresolved_terminal_obligations(self) -> List[ObligationRecord]:
        """Every currently-tracked obligation flagged terminal_required
        whose latest status is not SATISFIED (spec §42). VIOLATED, PENDING,
        and INDETERMINATE are all included deliberately - a caller invoking
        this is asking "is the whole run allowed to succeed right now,"
        and by the time that question is asked every subtask has already
        finished executing, so a still-PENDING terminal obligation is just
        as disqualifying as a VIOLATED one (nothing legitimately remains
        PENDING once there's no future stage left to satisfy it)."""
        result: List[ObligationRecord] = []
        for oid in self._history:
            rec = self.current(oid)
            if rec is not None and rec.terminal_required and rec.status != ObligationStatus.SATISFIED:
                result.append(rec)
        return result

    @staticmethod
    def _domain(obligation_id: str) -> str:
        parts = obligation_id.split(".")
        return ".".join(parts[:2])

    def relevant_for_preservation(
        self, kind: ObligationKind, violated_ids: Iterable[str],
    ) -> List[ObligationRecord]:
        """SATISFIED obligations of `kind` worth telling a repair prompt to
        preserve (spec §20) - deliberately NOT every currently-satisfied
        obligation, to keep the repair prompt bounded as a plan grows.
        Included when any of:
          - it just became SATISFIED on its own immediately-previous
            record (a repair that fixed it last time - the one most at
            risk of being silently regressed again this time);
          - it has already regressed at least once this run;
          - it shares an owner_subtask_id with a currently-violated
            obligation of the same kind;
          - it shares the same id "domain" (the id's first two
            dot-separated segments, e.g. "plan.file"/"plan.subtask") with a
            currently-violated obligation - a proxy for "same plan
            field/constraint family as what's currently broken"."""
        violated_records = [
            rec for rec in (self.current(oid) for oid in violated_ids) if rec is not None
        ]
        violated_owners = {
            rec.owner_subtask_id for rec in violated_records if rec.owner_subtask_id
        }
        violated_domains = {self._domain(rec.id) for rec in violated_records}
        regressed_ids = {reg.obligation_id for reg in self.regressions}
        result: List[ObligationRecord] = []
        for oid in self.ids_by_kind(kind):
            rec = self.current(oid)
            if rec is None or rec.status != ObligationStatus.SATISFIED:
                continue
            hist = self.history(oid)
            just_became_satisfied = len(hist) >= 2 and hist[-2].status != ObligationStatus.SATISFIED
            already_regressed = oid in regressed_ids
            same_owner = rec.owner_subtask_id is not None and rec.owner_subtask_id in violated_owners
            same_domain = self._domain(rec.id) in violated_domains
            if just_became_satisfied or already_regressed or same_owner or same_domain:
                result.append(rec)
        return result

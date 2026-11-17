"""S8 (C-07, C-08): the run's durable work ledger.

One logical ledger per top-level piece of work. It owns what a retry, a re-invocation, a crash or a resume must never
get back for free: the generation time already consumed, the unit invocations and Developer attempts already granted,
and each unit's retry-family history (the C-07 allowance identity: the normalized failure-family signature).

The document (``.kriya/control/work_ledger.json``, written through the control store, private and durable) is the
authority; the RunRecord's ``work_ledger`` field and any run event are derived from it.

Rules this module owns:

* **Grant before work.** A unit invocation or a Developer attempt is counted, and the count durably written, before
  the work starts. A grant that cannot be written is refused.
* **A crash never restores anything.** A ledger that still holds an open grant when a later run adopts it charges the
  grant's recorded time reservation on top of the consumption recorded at the grant.
* **A resume continues or is refused; only a new run starts over.** A run asked to resume adopts the ledger the
  workspace holds (the one that names the earlier run, when the resume identifies one). A ledger that is missing,
  unreadable or names another run REFUSES the resume (``ResumeLedgerRefused`` / the ``resume_refused`` result): the
  run grants nothing, calls no model and never becomes a fresh run. A fresh allowance is a new run, asked for as one.
  A workspace whose state predates the ledger has none, so its checkpoints are not resumable (nothing persisted
  before the ledger can reconstruct the consumption exactly).
* **The unit ceiling.** At most ``UNIT_INVOCATION_CEILING`` invocations per planned unit, the bound the enforce
  loop's own caps imply (one planned plus three cycles of owner recovery and consumer retry). Not a tuning number.

Pure state and its (de)serialization: the lease functions that read the clock and write the document are in
``kriya/control/run_coordinator.py``; the paths and the writer are ``kriya/control/persistence.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

WORK_LEDGER_SCHEMA_VERSION = 1
UNIT_INVOCATION_CEILING = 7
# The unit a run that is not a plan of units works on (the direct pipeline).
DIRECT_UNIT = "direct"

# Typed stop: the unit has used every invocation its ledger grants.
WORK_CEILING_EXHAUSTED = "WORK_CEILING_EXHAUSTED"
# Typed stop: the grant could not be durably recorded, so the work may not start.
WORK_LEDGER_NOT_PERSISTED = "WORK_LEDGER_NOT_PERSISTED"
# Typed stop: the run's resume was refused (below), so it is granted no work at all.
WORK_LEDGER_RESUME_REFUSED = "WORK_LEDGER_RESUME_REFUSED"
# What a ledger refusal's message starts with (the reason-code prefix the terminal classification keys on).
WORK_LEDGER_STOP_PREFIXES = (f"{WORK_CEILING_EXHAUSTED}:", f"{WORK_LEDGER_NOT_PERSISTED}:", f"{WORK_LEDGER_RESUME_REFUSED}:")
# Why a resume could not continue a ledger. Each one REFUSES the resume: the run is never continued as a fresh run.
WORK_LEDGER_MISSING = "WORK_LEDGER_MISSING"
WORK_LEDGER_UNREADABLE = "WORK_LEDGER_UNREADABLE"
WORK_LEDGER_NOT_THIS_WORK = "WORK_LEDGER_NOT_THIS_WORK"
RESUME_LEDGER_REFUSAL_CODES = (WORK_LEDGER_MISSING, WORK_LEDGER_UNREADABLE, WORK_LEDGER_NOT_THIS_WORK)


class ResumeLedgerRefused(Exception):
    """A requested resume cannot continue the work ledger that owns the earlier consumption (``reason_code`` is one of
    ``RESUME_LEDGER_REFUSAL_CODES``). Raised before any planning or model call; never caught to start a fresh run."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(resume_ledger_refusal_message(reason_code))
        self.reason_code = reason_code


def resume_ledger_refusal_message(reason_code: str) -> str:
    detail = {
        WORK_LEDGER_MISSING: "this workspace has no work ledger (it was removed, or the earlier run predates the ledger)",
        WORK_LEDGER_UNREADABLE: "the workspace's work ledger cannot be read as a ledger",
        WORK_LEDGER_NOT_THIS_WORK: "the workspace's work ledger does not name the run this resume continues",
    }.get(reason_code, "the workspace's work ledger cannot be continued")
    return (f"{reason_code}: resume refused - {detail}. A resume continues the consumed generation time, attempts and retry history of the "
            "earlier run and never resets them; nothing was generated. Start a new run (without --resume) for a new allowance.")


def resume_ledger_refused_result(reason_code: str, *, run_id: Optional[str]) -> Dict[str, Any]:
    """The typed ``resume_refused`` result (the shape PRD-008's refused resume already returns) for a ledger refusal."""
    return {
        "status": "resume_refused", "quality_gates_passed": False, "files": [], "reason_codes": [reason_code],
        "resume_decisions": [], "error": resume_ledger_refusal_message(reason_code), "run_id": run_id,
    }

GRANT_UNIT_INVOCATION = "unit_invocation"
GRANT_DEVELOPER_ATTEMPT = "developer_attempt"


class WorkLedgerError(ValueError):
    """A ledger document that is not a valid ledger."""


def _count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkLedgerError(f"work ledger {name} is not a non-negative integer: {value!r}")
    return value


def _seconds(value: Any, name: str) -> float:
    if type(value) not in (int, float) or value != value or value < 0:
        raise WorkLedgerError(f"work ledger {name} is not a non-negative number: {value!r}")
    return float(value)


@dataclass
class WorkLedger:
    ledger_id: str
    # Every run that worked under this ledger, oldest first; the last one is the current (or latest) run.
    runs: List[str]
    consumed_seconds: float = 0.0
    developer_attempts: int = 0
    unit_invocations: Dict[str, int] = field(default_factory=dict)
    # {"kind", "unit_id", "reserved_seconds"} while granted work is running; None between work items.
    open_grant: Optional[Dict[str, Any]] = None
    # unit id -> its retry-family state (workflow/retry_policy.py owns the meaning; opaque here).
    retry: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # The ledger this one displaced on disk (its ``to_dict``, never nested deeper), held while this ledger's run has not yet decided whether it resumes that earlier
    # work: a run may grant work (which writes this ledger) before it reaches its resume decision, and that write must not destroy what a resume would continue - not
    # even if the process dies in between. Dropped when the earlier ledger is adopted and when the run ends without adopting it.
    previous: Optional[Dict[str, Any]] = None

    @classmethod
    def new(cls, run_id: str) -> "WorkLedger":
        return cls(ledger_id=run_id, runs=[run_id])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": WORK_LEDGER_SCHEMA_VERSION,
            "ledger_id": self.ledger_id,
            "runs": list(self.runs),
            "consumed_seconds": self.consumed_seconds,
            "developer_attempts": self.developer_attempts,
            "unit_invocations": dict(self.unit_invocations),
            "open_grant": dict(self.open_grant) if self.open_grant is not None else None,
            "retry": {unit: dict(state) for unit, state in self.retry.items()},
            "previous": dict(self.previous) if self.previous is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "WorkLedger":
        """Strict: anything that is not exactly a ledger raises ``WorkLedgerError`` (never a partly trusted ledger)."""
        if not isinstance(data, Mapping):
            raise WorkLedgerError("work ledger document is not an object")
        if data.get("schema_version") != WORK_LEDGER_SCHEMA_VERSION:
            raise WorkLedgerError(f"unsupported work ledger schema_version {data.get('schema_version')!r}")
        ledger_id, runs = data.get("ledger_id"), data.get("runs")
        if not isinstance(ledger_id, str) or not ledger_id:
            raise WorkLedgerError("work ledger has no ledger_id")
        if not isinstance(runs, list) or not runs or not all(isinstance(run, str) and run for run in runs):
            raise WorkLedgerError("work ledger has no run history")
        invocations = data.get("unit_invocations")
        if not isinstance(invocations, Mapping) or not all(isinstance(unit, str) for unit in invocations):
            raise WorkLedgerError("work ledger unit_invocations is not a mapping of unit ids")
        open_grant = data.get("open_grant")
        if open_grant is not None:
            if not isinstance(open_grant, Mapping) or open_grant.get("kind") not in (GRANT_UNIT_INVOCATION, GRANT_DEVELOPER_ATTEMPT):
                raise WorkLedgerError("work ledger open_grant is not a grant")
            open_grant = {
                "kind": open_grant["kind"], "unit_id": str(open_grant.get("unit_id") or DIRECT_UNIT),
                "reserved_seconds": _seconds(open_grant.get("reserved_seconds"), "open_grant.reserved_seconds"),
            }
        retry = data.get("retry")
        if not isinstance(retry, Mapping) or not all(isinstance(unit, str) and isinstance(state, Mapping) for unit, state in retry.items()):
            raise WorkLedgerError("work ledger retry state is not a mapping of unit states")
        previous = data.get("previous")
        if previous is not None:
            if not isinstance(previous, Mapping) or previous.get("previous") is not None:
                raise WorkLedgerError("work ledger previous is not a single earlier ledger")
            previous = cls.from_dict(previous).to_dict()
        return cls(
            previous=previous,
            ledger_id=ledger_id, runs=list(runs),
            consumed_seconds=_seconds(data.get("consumed_seconds"), "consumed_seconds"),
            developer_attempts=_count(data.get("developer_attempts"), "developer_attempts"),
            unit_invocations={unit: _count(count, f"unit_invocations[{unit}]") for unit, count in invocations.items()},
            open_grant=open_grant,
            retry={unit: dict(state) for unit, state in retry.items()},
        )

    def continued_by(self, run_id: str) -> "WorkLedger":
        """This ledger as the run ``run_id`` continues it. An open grant means the earlier process ended inside granted
        work: its time reservation is charged (the consumption inside it was never recorded), and its count stays."""
        outstanding = float(self.open_grant["reserved_seconds"]) if self.open_grant is not None else 0.0
        return WorkLedger(
            ledger_id=self.ledger_id, runs=[*self.runs, run_id] if run_id not in self.runs else list(self.runs),
            consumed_seconds=self.consumed_seconds + outstanding, developer_attempts=self.developer_attempts,
            unit_invocations=dict(self.unit_invocations), open_grant=None,
            retry={unit: dict(state) for unit, state in self.retry.items()},
        )

    def absorb(self, other: "WorkLedger") -> None:
        """Add ``other``'s work to this ledger: everything it consumed and granted (an open grant's reservation included), its runs after this one's, and the
        retry state of units this ledger has none for. Used when one piece of work turns out to span two ledgers."""
        self.consumed_seconds += other.consumed_seconds + (float(other.open_grant["reserved_seconds"]) if other.open_grant is not None else 0.0)
        self.developer_attempts += other.developer_attempts
        for unit, count in other.unit_invocations.items():
            self.unit_invocations[unit] = self.unit_invocations.get(unit, 0) + count
        for unit, state in other.retry.items():
            self.retry.setdefault(unit, dict(state))
        self.runs.extend(run for run in other.runs if run not in self.runs)

    def folded(self) -> "WorkLedger":
        """This ledger with the one it displaced folded in. A ledger that still holds ``previous`` belongs to a run that ended (crashed) before it decided whether
        it resumed the earlier work; whoever continues either of them is charged for both."""
        if self.previous is None:
            return self.continued_by(self.runs[-1])
        merged = WorkLedger.from_dict(self.previous).continued_by(self.previous["runs"][-1])
        merged.absorb(self)
        return merged

    def invocation_refusal(self, unit_id: str) -> Optional[str]:
        """Why ``unit_id`` may not be invoked again under this ledger, else None."""
        used = self.unit_invocations.get(unit_id, 0)
        if used >= UNIT_INVOCATION_CEILING:
            return (f"{WORK_CEILING_EXHAUSTED}: unit {unit_id!r} has used all {UNIT_INVOCATION_CEILING} invocations its work "
                    f"ledger grants ({used} recorded, ledger {self.ledger_id}); a resume continues the same ledger.")
        return None

    def reference(self) -> Dict[str, Any]:
        """The RunRecord's derived view: identity and counts, no clock value."""
        return {
            "ledger_id": self.ledger_id,
            "resumed_from": self.runs[-2] if len(self.runs) > 1 else None,
            "unit_invocations": dict(sorted(self.unit_invocations.items())),
            "developer_attempts": self.developer_attempts,
        }

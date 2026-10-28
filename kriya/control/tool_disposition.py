"""Wave 2 Slice A (TOOL EXECUTION DISPOSITION): the operator's disposition of a plan TOOL subtask whose external effect may or may not have happened.

    inspect  - read-only: what is awaiting an operator, with the evidence kept for it;
    abandon  - terminal disposition: the subtask is never executed again and the plan cannot complete past it (``abandoned`` is not ``completed``);
    retry    - retry-as-NEW-attempt: re-arms the subtask (``pending``, attempt N+1) and nothing else.

Nothing here performs, schedules or authorizes an external effect, and no edge reaches ``completed``: a retry only re-arms, and the effect happens later through the
ordinary resume -> enforce loop -> ``SubtaskExecutor`` -> the tool's own authority path, which decides afresh with the authority current at THAT moment. This module is the
control owner behind ``kriya runs`` (a thin CLI); it is reachable from no tool, plugin, MCP or model-controlled path (pinned by tests/architecture-style import tests and a
contained-child test).

Every transition follows one sequence under the workspace run lock (the cross-command serialization; the control store's read-to-write digest is not one):
    1. take the canonical workspace run lock;     2. reload the authoritative ControlState (strict: an unreadable one is refused);
    3. validate the subtask, its CURRENT attempt and its disposition;
    4. durably append the operator's decision (with the prior state's digest and the prior attempt) to the decision ledger;
    5. durably persist the new state;             6. release the lock.
(W2-02: a DIRECT `kriya tools execute` run is disposed through the same sequence, but its authority is its RunRecord, never the ControlState and never the ledger:
    lock -> reload the RunRecord (strict) -> verify it is still the unresolved direct execution -> durable ledger AUDIT record -> durable RunRecord `abandoned` transition. The
    ledger is audit evidence only: no gate or status consults it, so an audit record without the RunRecord transition clears nothing. There is no direct-run retry: a new
    `kriya tools execute` is a new run identity with fresh confirmation, policy and authority.)
A crash between 4 and 5 leaves the record without the transition; re-running the same command finds its record (a key bound to run, subtask, action and the attempt it
disposes of), skips step 4 and completes step 5 - exactly one coherent disposition, never two retry attempts, and the prior attempt's evidence is never replaced (the ledger
is append-only). A crash before 4 changes nothing.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from kriya.control.decisions import DecisionLedger, load_decision_ledger
from kriya.control.persistence import (
    ControlStateUnreadableError,
    UnreadableRunRecordError,
    inspect_control_state,
    load_control_state,
    load_run_record,
    save_control_state,
    save_run_record,
    scan_run_records,
)
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.run_record import (
    TOOL_EXECUTION_ABANDONED,
    TOOL_EXECUTION_STARTED,
    IllegalRunTransitionError,
    RunRecord,
)
from kriya.control.state import (
    SUBTASK_STATE_TOOL_ABANDONED,
    SUBTASK_STATE_TOOL_FAILED,
    SUBTASK_STATE_TOOL_STARTED,
    ControlState,
)
from kriya.repository.atomic_write import (
    DurableWriteError as DurableWriteError,  # re-exported: what a durable disposition write raises (the CLI translates it)
)

ACTION_ABANDON = "abandon"
ACTION_RETRY = "retry"
ACTIONS = (ACTION_ABANDON, ACTION_RETRY)

DECISION_TYPE = "tool_disposition"

# the closed table of refusal reason codes (policy keys on reason_code)
CONFIRMATION_REQUIRED = "DISPOSITION_CONFIRMATION_REQUIRED"
CONTROL_STATE_UNREADABLE = "DISPOSITION_CONTROL_STATE_UNREADABLE"
NO_CONTROL_STATE = "DISPOSITION_NO_CONTROL_STATE"
SUBTASK_NOT_DISPOSABLE = "DISPOSITION_SUBTASK_NOT_DISPOSABLE"
STALE_ATTEMPT = "DISPOSITION_STALE_ATTEMPT"
LEGACY_ATTEMPT_UNVERSIONED = "DISPOSITION_LEGACY_ATTEMPT_UNVERSIONED"
STATE_CHANGED_SINCE_RECORDED = "DISPOSITION_STATE_CHANGED_SINCE_RECORDED"
# W2-02 (direct `kriya tools execute` runs)
RUN_NOT_FOUND = "DISPOSITION_RUN_NOT_FOUND"
RUN_RECORD_UNREADABLE = "DISPOSITION_RUN_RECORD_UNREADABLE"
NOT_AN_UNRESOLVED_DIRECT_EXECUTION = "DISPOSITION_NOT_AN_UNRESOLVED_DIRECT_EXECUTION"
REFUSAL_REASON_CODES = frozenset({
    CONFIRMATION_REQUIRED, CONTROL_STATE_UNREADABLE, NO_CONTROL_STATE, SUBTASK_NOT_DISPOSABLE, STALE_ATTEMPT, LEGACY_ATTEMPT_UNVERSIONED, STATE_CHANGED_SINCE_RECORDED,
    RUN_NOT_FOUND, RUN_RECORD_UNREADABLE, NOT_AN_UNRESOLVED_DIRECT_EXECUTION,
})

STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"
STATUS_REFUSED = "refused"

CLASSIFICATIONS = {
    SUBTASK_STATE_TOOL_STARTED: "UNRESOLVED: STARTED and never durably COMPLETED - the side effect may or may not have happened",
    SUBTASK_STATE_TOOL_FAILED: "FAILED_TERMINAL: the attempt failed after its side effect may have begun",
    SUBTASK_STATE_TOOL_ABANDONED: "ABANDONED: an operator disposed of it; it is never executed again",
}
_ELIGIBLE_STATES = {
    ACTION_ABANDON: (SUBTASK_STATE_TOOL_STARTED, SUBTASK_STATE_TOOL_FAILED),
    ACTION_RETRY: (SUBTASK_STATE_TOOL_STARTED, SUBTASK_STATE_TOOL_FAILED, SUBTASK_STATE_TOOL_ABANDONED),
}


@dataclass(frozen=True)
class ToolDispositionEntry:
    subtask_id: str
    state: str
    classification: str
    attempt: Optional[int]          # None: persisted without attempt identity = legacy/unversioned, NEVER silently attempt 1
    allowed_actions: Tuple[str, ...]
    history: Tuple[Dict[str, Any], ...] = ()

    @property
    def legacy_unversioned(self) -> bool:
        return self.attempt is None

    def to_dict(self) -> Dict[str, Any]:
        return {"subtask_id": self.subtask_id, "state": self.state, "classification": self.classification, "attempt": self.attempt,
                "legacy_unversioned": self.legacy_unversioned, "allowed_actions": list(self.allowed_actions), "history": [dict(h) for h in self.history]}


@dataclass(frozen=True)
class ToolDispositionReport:
    workspace: str
    run_id: Optional[str]
    entries: Tuple[ToolDispositionEntry, ...] = ()
    unreadable_control_state: Optional[Tuple[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"workspace": self.workspace, "run_id": self.run_id, "entries": [e.to_dict() for e in self.entries],
                "unreadable_control_state": list(self.unreadable_control_state) if self.unreadable_control_state else None}


@dataclass(frozen=True)
class DispositionResult:
    status: str
    action: str
    subtask_id: str
    reason_code: Optional[str] = None
    message: str = ""
    from_attempt: Optional[int] = None
    to_attempt: Optional[int] = None
    prior_state: Optional[str] = None
    new_state: Optional[str] = None
    disposition_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    run_id: Optional[str] = None            # W2-02: set for a direct-run disposition (subtask_id is then empty)

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_APPLIED, STATUS_ALREADY_APPLIED)

    def to_dict(self) -> Dict[str, Any]:
        payload = {"status": self.status, "action": self.action, "subtask_id": self.subtask_id, "reason_code": self.reason_code, "message": self.message,
                   "from_attempt": self.from_attempt, "to_attempt": self.to_attempt, "prior_state": self.prior_state, "new_state": self.new_state,
                   "disposition_id": self.disposition_id, "details": dict(self.details)}
        if self.run_id is not None:
            payload["run_id"] = self.run_id
        return payload


def _canonical(workspace_path: str) -> str:
    from kriya.control.recovery import canonical_workspace
    return canonical_workspace(workspace_path)


def _history(ledger: DecisionLedger, subtask_id: str) -> Tuple[Dict[str, Any], ...]:
    rows: List[Dict[str, Any]] = []
    for decision in ledger.all():
        if decision.fields.get("subtask_id") != subtask_id:
            continue
        if decision.type in ("subtask_attempt", DECISION_TYPE) and (decision.type == DECISION_TYPE or decision.fields.get("execution_method") == "tool"):
            rows.append({"type": decision.type, **{k: v for k, v in decision.fields.items() if k != "subtask_id"}})
    return tuple(rows)


def inspect_tool_dispositions(workspace_path: str) -> ToolDispositionReport:
    """Read-only. Lists only positively identified TOOL lifecycle records (the D1/Slice A markers); a plain "failed"/"needs_review" TOOL record from a failure PROVEN to be before
    its effect is re-armable through the ordinary resume path and is not an operator matter, and a non-TOOL record is never reported."""
    canonical = _canonical(workspace_path)
    state, unreadable = inspect_control_state(canonical)
    if unreadable is not None:
        return ToolDispositionReport(workspace=canonical, run_id=None, unreadable_control_state=unreadable)
    if state is None:
        return ToolDispositionReport(workspace=canonical, run_id=None)
    ledger = load_decision_ledger(canonical)
    entries = []
    for subtask_id, value in sorted(state.subtask_states.items()):
        if value not in CLASSIFICATIONS:
            continue
        attempt = state.tool_attempts.get(subtask_id)
        allowed = tuple(action for action in ACTIONS if value in _ELIGIBLE_STATES[action] and not (action == ACTION_RETRY and attempt is None))
        entries.append(ToolDispositionEntry(subtask_id=subtask_id, state=value, classification=CLASSIFICATIONS[value], attempt=attempt,
                                            allowed_actions=allowed, history=_history(ledger, subtask_id)))
    return ToolDispositionReport(workspace=canonical, run_id=state.run_id, entries=tuple(entries))


def _disposition_id(run_id: str, subtask_id: str, action: str, from_attempt: Optional[int]) -> str:
    label = "legacy" if from_attempt is None else str(from_attempt)
    return hashlib.sha256(f"{run_id}|{subtask_id}|{action}|{label}".encode("utf-8")).hexdigest()[:24]


def _refused(action: str, subtask_id: str, code: str, message: str, **kwargs: Any) -> DispositionResult:
    assert code in REFUSAL_REASON_CODES, code
    return DispositionResult(status=STATUS_REFUSED, action=action, subtask_id=subtask_id, reason_code=code, message=message, **kwargs)


def apply_disposition(workspace_path: str, action: str, subtask_id: str, *, expected_attempt: Optional[int], operator: str, confirmed: bool) -> DispositionResult:
    """abandon / retry one TOOL subtask. ``expected_attempt`` is the attempt the operator inspected (None only for a legacy/unversioned record); a mismatch is refused, so a
    command can never act on a state other than the one that was reviewed. Raises WorkspaceLockHeldError when a run owns the workspace (never blocks)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown disposition action {action!r}")
    if not confirmed:
        return _refused(action, subtask_id, CONFIRMATION_REQUIRED, f"{action} needs an explicit confirmation; nothing was changed.")
    canonical = _canonical(workspace_path)
    with acquire_run_lock(canonical, run_id=f"disposition-{uuid.uuid4().hex[:12]}"):          # 1
        try:
            state = load_control_state(canonical)                                              # 2 (strict: an unreadable state is never "absent")
        except ControlStateUnreadableError as error:
            if error.durable_write_error is not None:      # visible but not confirmed durable: the durable-write failure it is (exit 4), not a corrupt-state refusal
                raise error.durable_write_error from error
            return _refused(action, subtask_id, CONTROL_STATE_UNREADABLE, str(error))
        if state is None:
            return _refused(action, subtask_id, NO_CONTROL_STATE, "this workspace has no control state; there is nothing to dispose of.")
        return _apply_locked(canonical, state, action, subtask_id, expected_attempt, operator)


def _apply_locked(canonical: str, state: ControlState, action: str, subtask_id: str, expected_attempt: Optional[int], operator: str) -> DispositionResult:
    current = state.subtask_states.get(subtask_id)
    attempt_now = state.tool_attempts.get(subtask_id)
    disposition_id = _disposition_id(state.run_id, subtask_id, action, expected_attempt)
    ledger = load_decision_ledger(canonical)
    recorded = next((d for d in ledger.filter_by_type(DECISION_TYPE) if d.fields.get("disposition_id") == disposition_id), None)

    eligible = current in _ELIGIBLE_STATES[action] and attempt_now == expected_attempt                                       # 3
    if not eligible:
        if recorded is not None:        # this very disposition was already applied (and the state has moved on): idempotent success, nothing is changed
            return DispositionResult(status=STATUS_ALREADY_APPLIED, action=action, subtask_id=subtask_id, from_attempt=expected_attempt,
                                     to_attempt=recorded.fields.get("to_attempt"), prior_state=recorded.fields.get("prior_state"),
                                     new_state=recorded.fields.get("new_state"), disposition_id=disposition_id, message="already applied; nothing changed.")
        if current not in _ELIGIBLE_STATES[action]:
            return _refused(action, subtask_id, SUBTASK_NOT_DISPOSABLE,
                            f"subtask {subtask_id!r} is {current!r}: only {', '.join(_ELIGIBLE_STATES[action])} can be {action}ed (see `kriya runs tool-inspect`).", prior_state=current)
        return _refused(action, subtask_id, STALE_ATTEMPT,
                        f"subtask {subtask_id!r} is at attempt {attempt_now!r}, not the attempt {expected_attempt!r} you inspected; inspect again.", prior_state=current,
                        from_attempt=attempt_now)
    if action == ACTION_RETRY and attempt_now is None:
        return _refused(action, subtask_id, LEGACY_ATTEMPT_UNVERSIONED,
                        f"subtask {subtask_id!r} was persisted before attempt identity existed (legacy/unversioned); it is never silently attempt 1. Abandon it instead.",
                        prior_state=current)

    new_state = SUBTASK_STATE_TOOL_ABANDONED if action == ACTION_ABANDON else "pending"
    to_attempt = attempt_now if action == ACTION_ABANDON else (attempt_now or 0) + 1
    digest = state.content_hash()
    if recorded is not None:
        if recorded.fields.get("prior_state_digest") != digest:      # a crash left a record, but the state it was made against is not the state now: do not guess
            return _refused(action, subtask_id, STATE_CHANGED_SINCE_RECORDED,
                            "a record of this disposition exists but the control state changed since; inspect the decision ledger before acting.", prior_state=current)
    else:
        ledger.append_to_file(canonical, ledger.record(                                                                        # 4: durable, append-only
            DECISION_TYPE, disposition_id=disposition_id, subtask_id=subtask_id, action=action, operator=operator, run_id=state.run_id,
            from_attempt=attempt_now, to_attempt=to_attempt, prior_state=current, new_state=new_state, prior_state_digest=digest,
            prior_history=list(_history(ledger, subtask_id))), durable=True)
    updated = state.with_updates(
        subtask_states={**state.subtask_states, subtask_id: new_state},
        tool_attempts={**state.tool_attempts, subtask_id: to_attempt} if action == ACTION_RETRY else state.tool_attempts,
    )
    save_control_state(canonical, updated, durable=True)                                                                         # 5
    return DispositionResult(status=STATUS_APPLIED, action=action, subtask_id=subtask_id, from_attempt=attempt_now, to_attempt=to_attempt, prior_state=current,
                             new_state=new_state, disposition_id=disposition_id,
                             message=("re-armed as attempt %d; nothing was executed - the next resume runs it through the normal authority path." % to_attempt)
                             if action == ACTION_RETRY else "abandoned; it will not be executed again and the plan cannot complete past it.")


# ------------------------------------------------------------------ W2-02: a DIRECT `kriya tools execute` run
DIRECT_KIND = "direct_run"


@dataclass(frozen=True)
class DirectRunEntry:
    """What an operator needs to decide about one direct tool run, read from its RunRecord (the single authority). Nothing here claims whether the external effect happened."""
    run_id: str
    tool: str
    lifecycle_state: str
    execution_state: str                     # the RunRecord.tool_execution state as recorded (started | completed | failed | abandoned | <unknown>)
    unresolved: bool
    classification: str
    revision: int
    created_at: str
    updated_at: str
    commit_result: Optional[str]
    terminal_status: Optional[str]
    allowed_actions: Tuple[str, ...]
    disposition: Optional[Dict[str, Any]] = None
    history: Tuple[Dict[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"run_id": self.run_id, "tool": self.tool, "lifecycle_state": self.lifecycle_state, "execution_state": self.execution_state, "unresolved": self.unresolved,
                "classification": self.classification, "revision": self.revision, "created_at": self.created_at, "updated_at": self.updated_at,
                "commit_result": self.commit_result, "terminal_status": self.terminal_status, "allowed_actions": list(self.allowed_actions),
                "disposition": dict(self.disposition) if self.disposition else None, "history": [dict(h) for h in self.history]}


@dataclass(frozen=True)
class DirectRunReport:
    workspace: str
    entry: Optional[DirectRunEntry] = None
    reason_code: Optional[str] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"workspace": self.workspace, "entry": self.entry.to_dict() if self.entry else None, "reason_code": self.reason_code, "message": self.message}


_DIRECT_UNRESOLVED_CLASSIFICATION = ("UNRESOLVED: the direct tool run was STARTED and never durably resolved - the side effect may or may not have happened "
                                     "(this is not inferred either way)")
_DIRECT_ABANDONED_CLASSIFICATION = ("ABANDONED: an operator intentionally stopped recovery of this unresolved execution; it does not mean the effect never started, failed or "
                                    "completed")


def _direct_history(ledger: DecisionLedger, run_id: str) -> Tuple[Dict[str, Any], ...]:
    return tuple({"type": d.type, **{k: v for k, v in d.fields.items() if k not in ("run_id", "prior_tool_execution")}}
                 for d in ledger.filter_by_type(DECISION_TYPE) if d.fields.get("kind") == DIRECT_KIND and d.fields.get("run_id") == run_id)


def _direct_entry(record: RunRecord, ledger: DecisionLedger) -> DirectRunEntry:
    execution = record.tool_execution if isinstance(record.tool_execution, dict) else {}
    state = str(execution.get("state")) if execution else ("<none>" if record.tool_execution is None else "<malformed>")
    unresolved = record.unresolved_tool_execution is not None
    startedish = execution.get("state") == TOOL_EXECUTION_STARTED
    if startedish:
        classification, allowed = _DIRECT_UNRESOLVED_CLASSIFICATION, (ACTION_ABANDON,)
    elif state == TOOL_EXECUTION_ABANDONED:
        classification, allowed = _DIRECT_ABANDONED_CLASSIFICATION, ()
    elif unresolved:
        classification, allowed = "UNRESOLVED: the recorded tool execution state is unknown or malformed - inspect the run record by hand; it is not abandonable here", ()
    else:
        classification, allowed = f"NOT UNRESOLVED: tool execution state {state!r}; nothing awaits an operator", ()
    disposition = execution.get("disposition") if state == TOOL_EXECUTION_ABANDONED and isinstance(execution.get("disposition"), dict) else None
    return DirectRunEntry(run_id=record.run_id, tool=str(execution.get("tool") or "<none>"), lifecycle_state=record.lifecycle_state.value, execution_state=state,
                          unresolved=unresolved, classification=classification, revision=record.revision, created_at=record.created_at, updated_at=record.updated_at,
                          commit_result=record.commit_result, terminal_status=record.terminal_status, allowed_actions=allowed, disposition=disposition,
                          history=_direct_history(ledger, record.run_id))


def inspect_direct_run(workspace_path: str, run_id: str) -> DirectRunReport:
    """Read-only. The authoritative RunRecord of one run, as the operator needs it to decide. A run that is not a positively identified direct tool execution (no
    ``tool_execution`` - including every pre-D1 record) is reported as such and is never reinterpreted as one."""
    canonical = _canonical(workspace_path)
    try:
        record = load_run_record(canonical, run_id)
    except ValueError as error:                     # not a valid run id
        return DirectRunReport(workspace=canonical, reason_code=RUN_NOT_FOUND, message=str(error))
    except UnreadableRunRecordError as error:
        return DirectRunReport(workspace=canonical, reason_code=RUN_RECORD_UNREADABLE, message=str(error))
    if record is None:
        return DirectRunReport(workspace=canonical, reason_code=RUN_NOT_FOUND, message=f"no run record {run_id!r} exists in this workspace.")
    return DirectRunReport(workspace=canonical, entry=_direct_entry(record, load_decision_ledger(canonical)))


def unresolved_direct_runs(workspace_path: str) -> Tuple[DirectRunEntry, ...]:
    """Read-only: every readable RunRecord that is an unresolved direct tool execution (the same RunRecord property the mutating-run gate reads)."""
    canonical = _canonical(workspace_path)
    ledger = load_decision_ledger(canonical)
    return tuple(_direct_entry(record, ledger) for record in scan_run_records(canonical).records if record.unresolved_tool_execution is not None)


def _direct_disposition_id(run_id: str) -> str:
    return hashlib.sha256(f"{run_id}|{ACTION_ABANDON}|{DIRECT_KIND}".encode("utf-8")).hexdigest()[:24]


def apply_direct_abandon(workspace_path: str, run_id: str, *, operator: str, confirmed: bool) -> DispositionResult:
    """Abandon one unresolved DIRECT tool run: the operator intentionally stops recovery of it. Performs no external effect and reaches no completed state. Raises
    WorkspaceLockHeldError when a run owns the workspace (never blocks) and lets a DurableWriteError (audit or RunRecord) propagate with nothing further done."""
    if not confirmed:
        return _direct_refused(run_id, CONFIRMATION_REQUIRED, "abandon needs an explicit confirmation; nothing was changed.")
    canonical = _canonical(workspace_path)
    with acquire_run_lock(canonical, run_id=f"disposition-{uuid.uuid4().hex[:12]}"):                      # 1
        try:
            record = load_run_record(canonical, run_id)                                                      # 2 (strict)
        except ValueError as error:
            return _direct_refused(run_id, RUN_NOT_FOUND, str(error))
        except UnreadableRunRecordError as error:
            if error.durable_write_error is not None:      # see apply_disposition
                raise error.durable_write_error from error
            return _direct_refused(run_id, RUN_RECORD_UNREADABLE, str(error))
        if record is None:
            return _direct_refused(run_id, RUN_NOT_FOUND, f"no run record {run_id!r} exists in this workspace.")
        return _abandon_direct_locked(canonical, record, operator)


def _direct_refused(run_id: str, code: str, message: str, **kwargs: Any) -> DispositionResult:
    assert code in REFUSAL_REASON_CODES, code
    return DispositionResult(status=STATUS_REFUSED, action=ACTION_ABANDON, subtask_id="", run_id=run_id, reason_code=code, message=message, **kwargs)


def _abandon_direct_locked(canonical: str, record: RunRecord, operator: str) -> DispositionResult:
    from datetime import datetime, timezone

    run_id = record.run_id
    disposition_id = _direct_disposition_id(run_id)
    execution = record.tool_execution if isinstance(record.tool_execution, dict) else None
    prior_state = str(execution.get("state")) if execution else None
    if execution is not None and execution.get("state") == TOOL_EXECUTION_ABANDONED and (execution.get("disposition") or {}).get("disposition_id") == disposition_id:
        return DispositionResult(status=STATUS_ALREADY_APPLIED, action=ACTION_ABANDON, subtask_id="", run_id=run_id, prior_state=TOOL_EXECUTION_STARTED,
                                 new_state=TOOL_EXECUTION_ABANDONED, disposition_id=disposition_id, message="already abandoned; nothing changed.")
    if execution is None or execution.get("state") != TOOL_EXECUTION_STARTED:
        return _direct_refused(run_id, NOT_AN_UNRESOLVED_DIRECT_EXECUTION,
                               f"run {run_id!r} is not a positively identified, unresolved direct tool execution (tool execution state: {prior_state!r}); only a run whose "
                               "tool_execution is STARTED can be abandoned, and no other record is ever reinterpreted as one.", prior_state=prior_state)
    ledger = load_decision_ledger(canonical)
    recorded = next((d for d in ledger.filter_by_type(DECISION_TYPE) if d.fields.get("disposition_id") == disposition_id), None)
    if recorded is not None:
        # an AUDIT record from a crashed earlier attempt: it authorizes nothing (no gate reads the ledger). It is reused only if it was made against THIS RunRecord revision.
        if recorded.fields.get("prior_revision") != record.revision:
            return _direct_refused(run_id, STATE_CHANGED_SINCE_RECORDED,
                                   "an audit record of this disposition exists but the run record changed since; inspect the decision ledger before acting.", prior_state=prior_state)
    else:
        ledger.append_to_file(canonical, ledger.record(                                                                        # 3: durable, append-only audit
            DECISION_TYPE, disposition_id=disposition_id, kind=DIRECT_KIND, run_id=run_id, tool=str(execution.get("tool") or ""), action=ACTION_ABANDON, operator=operator,
            prior_revision=record.revision, prior_lifecycle_state=record.lifecycle_state.value, prior_updated_at=record.updated_at, record_created_at=record.created_at,
            prior_tool_execution=dict(execution), new_state=TOOL_EXECUTION_ABANDONED), durable=True)
    try:
        updated = record.abandon_tool_execution({"disposition_id": disposition_id, "operator": operator, "action": ACTION_ABANDON,
                                                 "at": datetime.now(timezone.utc).isoformat()})
    except IllegalRunTransitionError as error:
        return _direct_refused(run_id, NOT_AN_UNRESOLVED_DIRECT_EXECUTION, str(error), prior_state=prior_state)
    save_run_record(canonical, updated, expected_revision=record.revision, durable=True)                                     # 4: durable RunRecord disposition
    return DispositionResult(status=STATUS_APPLIED, action=ACTION_ABANDON, subtask_id="", run_id=run_id, prior_state=TOOL_EXECUTION_STARTED, new_state=TOOL_EXECUTION_ABANDONED,
                             disposition_id=disposition_id, message="abandoned; the workspace no longer treats it as unresolved, nothing was executed and the effect is not undone.")

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
    inspect_control_state,
    load_control_state,
    save_control_state,
)
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.state import (
    SUBTASK_STATE_TOOL_ABANDONED,
    SUBTASK_STATE_TOOL_FAILED,
    SUBTASK_STATE_TOOL_STARTED,
    ControlState,
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
REFUSAL_REASON_CODES = frozenset({
    CONFIRMATION_REQUIRED, CONTROL_STATE_UNREADABLE, NO_CONTROL_STATE, SUBTASK_NOT_DISPOSABLE, STALE_ATTEMPT, LEGACY_ATTEMPT_UNVERSIONED, STATE_CHANGED_SINCE_RECORDED,
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

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_APPLIED, STATUS_ALREADY_APPLIED)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "action": self.action, "subtask_id": self.subtask_id, "reason_code": self.reason_code, "message": self.message,
                "from_attempt": self.from_attempt, "to_attempt": self.to_attempt, "prior_state": self.prior_state, "new_state": self.new_state,
                "disposition_id": self.disposition_id, "details": dict(self.details)}


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

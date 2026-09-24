"""Canonical, content-free lifecycle record for one mutating Kriya run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

RUN_RECORD_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLifecycle(str, Enum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    PLANNING = "PLANNING"
    CANDIDATE = "CANDIDATE"
    VERIFYING = "VERIFYING"
    COMMIT_ELIGIBLE = "COMMIT_ELIGIBLE"
    COMMITTED = "COMMITTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    UNCERTAIN = "UNCERTAIN"


TERMINAL_LIFECYCLES = frozenset({
    RunLifecycle.SUCCESS, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN,
})

_ALLOWED_TRANSITIONS = {
    RunLifecycle.NEW: {RunLifecycle.RUNNING, RunLifecycle.PLANNING, RunLifecycle.FAILURE},
    RunLifecycle.RUNNING: {
        RunLifecycle.PLANNING, RunLifecycle.CANDIDATE, RunLifecycle.VERIFYING,
        RunLifecycle.COMMIT_ELIGIBLE, RunLifecycle.COMMITTED,
        RunLifecycle.SUCCESS, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN,
    },
    RunLifecycle.PLANNING: {RunLifecycle.CANDIDATE, RunLifecycle.FAILURE},
    RunLifecycle.CANDIDATE: {RunLifecycle.VERIFYING, RunLifecycle.FAILURE},
    RunLifecycle.VERIFYING: {
        RunLifecycle.COMMIT_ELIGIBLE, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN,
    },
    RunLifecycle.COMMIT_ELIGIBLE: {
        RunLifecycle.COMMITTED, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN,
    },
    RunLifecycle.COMMITTED: {
        RunLifecycle.SUCCESS, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN,
    },
}


class IllegalRunTransitionError(RuntimeError):
    """The requested lifecycle transition is not permitted."""


@dataclass(frozen=True)
class RunRecord:
    schema_version: int
    revision: int
    run_id: str
    workspace_identity: str
    base_source_revision: Optional[str]
    base_tree_hash: Optional[str]
    lifecycle_state: RunLifecycle
    effective_config_fingerprint: Optional[str] = None
    model_runtime_fingerprint_ids: List[str] = field(default_factory=list)
    goal_hash: Optional[str] = None
    approved_plan_hash: Optional[str] = None
    obligation_ledger_revision: Optional[int] = None
    obligation_ledger_hash: Optional[str] = None
    candidate_revision: Optional[str] = None
    candidate_hash: Optional[str] = None
    verification_evidence_ids: List[str] = field(default_factory=list)
    retry_state_reference: Optional[str] = None
    retry_counters: Dict[str, int] = field(default_factory=dict)
    commit_intent: Optional[str] = None
    # PRD-004: the commit-evidence transaction this run's intent refers to
    # (the controller's run_id can differ from the RunRecord's run_id).
    commit_transaction_id: Optional[str] = None
    commit_result: Optional[str] = None
    terminal_status: Optional[str] = None
    store_revisions: Dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def new(
        cls, run_id: str, workspace_identity: str,
        base_source_revision: Optional[str], base_tree_hash: Optional[str],
    ) -> "RunRecord":
        return cls(
            schema_version=RUN_RECORD_SCHEMA_VERSION,
            revision=1,
            run_id=run_id,
            workspace_identity=workspace_identity,
            base_source_revision=base_source_revision,
            base_tree_hash=base_tree_hash,
            lifecycle_state=RunLifecycle.NEW,
        )

    @property
    def terminal(self) -> bool:
        return self.lifecycle_state in TERMINAL_LIFECYCLES

    def transition(self, state: RunLifecycle, **updates: Any) -> "RunRecord":
        if self.terminal or state not in _ALLOWED_TRANSITIONS.get(self.lifecycle_state, set()):
            raise IllegalRunTransitionError(
                f"illegal run transition {self.lifecycle_state.value}->{state.value}"
            )
        terminal_status = updates.pop("terminal_status", None)
        if state in TERMINAL_LIFECYCLES and terminal_status is None:
            terminal_status = state.value
        return replace(
            self,
            lifecycle_state=state,
            revision=self.revision + 1,
            terminal_status=terminal_status,
            updated_at=_now(),
            **updates,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["lifecycle_state"] = self.lifecycle_state.value
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RunRecord":
        version = payload.get("schema_version")
        if version != RUN_RECORD_SCHEMA_VERSION:
            raise ValueError(f"unsupported RunRecord schema_version {version!r}")
        data = dict(payload)
        data.pop("_workspace", None)
        data["lifecycle_state"] = RunLifecycle(data["lifecycle_state"])
        return cls(**data)

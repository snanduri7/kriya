"""Canonical, content-free lifecycle record for one mutating Kriya run.

A ``RunRecord`` owns lifecycle identity only: which run, against which base,
under which configuration, what it was approved to do, what it committed,
and how it ended. It references the specialized evidence stores (see
``STORE_CLASSIFICATION``) by hash/ID and never copies their payloads, raw
prompts, or source contents.

Commits are modelled explicitly as ``commits`` cycles because one run can
make several real-workspace commits (a milestone sequence commits once per
milestone). Each cycle is ``{transaction_id, intent, candidate_hash,
result}``; a cycle whose ``result`` is still ``None`` is an unsettled commit
- a crash there means the workspace state is unknown, never "not started".

Schema 3 (PRD-008) adds the RECOVERED terminal lifecycle and its ``recovery``
provenance. RECOVERED says only HOW the record ended - explicit, evidence-based
``kriya runs recover`` - never that the run succeeded: ``commit_result`` still
says what reached the workspace and ``terminal_status`` is NEEDS_REVIEW or
FAILURE, because the run's post-commit steps never ran.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

RUN_RECORD_SCHEMA_VERSION = 3


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
    # Settled by `kriya runs recover` (recover()); never SUCCESS.
    RECOVERED = "RECOVERED"


TERMINAL_LIFECYCLES = frozenset({
    RunLifecycle.SUCCESS, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN, RunLifecycle.RECOVERED,
})
# Records recover() may settle: a run that died (non-terminal, which is only
# provable while the caller holds the workspace lock) or one that ended with
# its commit state unknown. SUCCESS/FAILURE are already settled.
_RECOVERABLE_TERMINALS = frozenset({RunLifecycle.UNCERTAIN})
TERMINAL_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
# Same word an ordinary failed run records (its lifecycle value).
TERMINAL_STATUS_FAILURE = RunLifecycle.FAILURE.value

# Settled results of one commit cycle.
COMMIT_COMMITTED = "COMMITTED"
COMMIT_ROLLED_BACK = "ROLLED_BACK"
COMMIT_NOT_COMMITTED = "NOT_COMMITTED"
COMMIT_UNCERTAIN = "UNCERTAIN"
_CYCLE_RESULTS = frozenset({
    COMMIT_COMMITTED, COMMIT_ROLLED_BACK, COMMIT_NOT_COMMITTED, COMMIT_UNCERTAIN,
})
# Run-level summaries that only exist without/over commit cycles.
COMMIT_NONE = "NO_COMMIT"  # no Kriya commit transaction was made by this run
COMMIT_PARTIAL = "PARTIALLY_COMMITTED"  # some cycles committed, a later one did not

# Forward stage progress plus the always-available fail-closed exits. Stage
# markers are progress evidence; COMMIT_ELIGIBLE/COMMITTED are reachable only
# through begin_commit()/settle_commit(), which carry the commit invariants.
_FAIL_EXITS = {RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN}
_STAGE_TRANSITIONS = {
    RunLifecycle.NEW: {RunLifecycle.RUNNING},
    RunLifecycle.RUNNING: {RunLifecycle.PLANNING, RunLifecycle.CANDIDATE, RunLifecycle.SUCCESS},
    RunLifecycle.PLANNING: {RunLifecycle.CANDIDATE, RunLifecycle.SUCCESS},
    RunLifecycle.CANDIDATE: {RunLifecycle.VERIFYING, RunLifecycle.SUCCESS},
    RunLifecycle.VERIFYING: {RunLifecycle.CANDIDATE, RunLifecycle.SUCCESS},
    RunLifecycle.COMMIT_ELIGIBLE: set(),
    # A later milestone/subtask may plan and build another candidate.
    RunLifecycle.COMMITTED: {RunLifecycle.PLANNING, RunLifecycle.CANDIDATE, RunLifecycle.SUCCESS},
}
_COMMIT_SOURCES = frozenset({
    RunLifecycle.RUNNING, RunLifecycle.PLANNING, RunLifecycle.CANDIDATE,
    RunLifecycle.VERIFYING, RunLifecycle.COMMITTED,
})

# Fields a same-state annotate() may set: evidence references only, never
# lifecycle or commit state.
_ANNOTATABLE_FIELDS = frozenset({
    "effective_config_fingerprint", "model_runtime_fingerprint_ids", "goal_hash",
    "approved_plan_hash", "obligation_ledger_revision", "obligation_ledger_hash",
    "candidate_hash", "verification_evidence_ids", "retry_state_reference",
    "retry_counters", "resume_decision", "milestone_reuse", "active_work_unit",
})

# Requirement 2: every persistent store is authoritative for its own content,
# derived from a RunRecord revision, or independent of run lifecycle (never
# consulted for lifecycle/commit truth, so not stamped). Derived stores stamp
# ``_run_record: {classification, run_id, revision}`` on every write
# (kriya/control/persistence.py, kriya/control/decisions.py,
# kriya/workflow/checkpoint.py), so each identifies the revision it derives from.
STORE_CLASSIFICATION: Dict[str, str] = {
    "run_record": "authoritative (run lifecycle, commit intent/result)",
    "commit_evidence": "authoritative (per-transaction commit state, edit_safety.py)",
    "control_state": "derived",
    "approved_plan": "derived",
    "contract_registry": "derived",
    "artifact_registry": "derived",
    "decision_ledger": "derived",
    "checkpoint": "derived",
    # Not stamped with a RunRecord revision, and never consulted for run
    # lifecycle or commit truth:
    "trace": "independent observability (traces.db, keyed by trace run_id)",
    "milestone_run_state": "independent (milestone plan progress, .kriya/milestones)",
    "proposal_store": "independent (review proposals, outside run lifecycle)",
    "knowledge_staging": "independent (staged lessons/skills awaiting approval)",
}


class IllegalRunTransitionError(RuntimeError):
    """The requested lifecycle transition is not permitted."""


class UnsupportedRunRecordError(ValueError):
    """A persisted record's schema is unknown or malformed (fail closed)."""


def summarize_commit_result(commits: List[Dict[str, Any]]) -> str:
    """One run-level commit result derived from the recorded cycles."""
    if not commits:
        return COMMIT_NONE
    results = [cycle.get("result") for cycle in commits]
    if any(result is None or result == COMMIT_UNCERTAIN for result in results):
        return COMMIT_UNCERTAIN
    # A cycle that did not commit left the workspace unchanged, so a later
    # successful retry makes the run COMMITTED; committed work followed by a
    # final cycle that did not commit is PARTIALLY_COMMITTED.
    if results[-1] == COMMIT_COMMITTED:
        return COMMIT_COMMITTED
    if COMMIT_COMMITTED in results:
        return COMMIT_PARTIAL
    return results[-1]


@dataclass(frozen=True)
class RunRecord:
    schema_version: int
    revision: int
    run_id: str
    workspace_identity: str
    base_source_revision: Optional[str]
    base_tree_hash: Optional[str]
    lifecycle_state: RunLifecycle
    # Same fingerprint the resume checkpoint stores (checkpoint.py's
    # compute_config_fingerprint over kernel.config.model_dump()).
    effective_config_fingerprint: Optional[str] = None
    # Runtime model digests are owned by PRD-013/014; until then this stays
    # empty and consumers must treat empty as UNVERIFIED, never as a match.
    model_runtime_fingerprint_ids: List[str] = field(default_factory=list)
    goal_hash: Optional[str] = None
    approved_plan_hash: Optional[str] = None
    obligation_ledger_revision: Optional[int] = None
    obligation_ledger_hash: Optional[str] = None
    # Digest of the exact candidate (path, bytes revision, mode, delete) most
    # recently made commit-eligible.
    candidate_hash: Optional[str] = None
    verification_evidence_ids: List[str] = field(default_factory=list)
    retry_state_reference: Optional[str] = None
    retry_counters: Dict[str, int] = field(default_factory=dict)
    # PRD-008: what this run reused from a checkpoint (ResumePlan.to_dict()),
    # when it was asked to resume. Optional, so v2 records without it load.
    resume_decision: Optional[Dict[str, Any]] = None
    # PRD-008 S4b: why each already-completed milestone was skipped or rerun
    # (kriya/workflow/milestone_completion.py). Optional, like resume_decision.
    milestone_reuse: Optional[Dict[str, Any]] = None
    # PRD-008 S4c: the unit of work (milestone group/id/definition digest,
    # or the integration pass) currently executing. begin_commit copies it
    # into each cycle, so a commit is attributable to its milestone from the
    # moment its intent is durable - before any workspace byte changes.
    active_work_unit: Optional[Dict[str, Any]] = None
    # Schema 3: provenance of an explicit `kriya runs recover` settlement.
    recovery: Optional[Dict[str, Any]] = None
    commits: List[Dict[str, Any]] = field(default_factory=list)
    # The CURRENT commit cycle (reset by each begin_commit); run-level
    # commit_result is the summary over ``commits`` once terminal.
    commit_intent: Optional[str] = None
    commit_transaction_id: Optional[str] = None
    commit_result: Optional[str] = None
    terminal_status: Optional[str] = None
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

    @property
    def unsettled_commits(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(cycle for cycle in self.commits if cycle.get("result") is None)

    @property
    def commit_state_unknown(self) -> bool:
        """True when the workspace may hold a partial commit from this run."""
        return (
            self.lifecycle_state == RunLifecycle.UNCERTAIN
            or bool(self.unsettled_commits)
            or any(cycle.get("result") == COMMIT_UNCERTAIN for cycle in self.commits)
        )

    def _advance(self, state: RunLifecycle, **updates: Any) -> "RunRecord":
        return replace(
            self, lifecycle_state=state, revision=self.revision + 1,
            updated_at=_now(), **updates,
        )

    def _refuse(self, state: RunLifecycle, why: str = "") -> IllegalRunTransitionError:
        suffix = f": {why}" if why else ""
        return IllegalRunTransitionError(
            f"illegal run transition {self.lifecycle_state.value}->{state.value}{suffix}"
        )

    def transition(self, state: RunLifecycle, **updates: Any) -> "RunRecord":
        """A stage marker or terminal transition (commits use begin/settle)."""
        if self.terminal:
            raise self._refuse(state, "record is terminal")
        unknown = set(updates) - _ANNOTATABLE_FIELDS - {"commit_result", "terminal_status"}
        if unknown:
            raise IllegalRunTransitionError(f"transition cannot set {sorted(unknown)}")
        if state in (RunLifecycle.COMMIT_ELIGIBLE, RunLifecycle.COMMITTED):
            raise self._refuse(state, "use begin_commit()/settle_commit()")
        if state in _FAIL_EXITS:
            return self._terminate(state, **updates)
        if state not in _STAGE_TRANSITIONS.get(self.lifecycle_state, set()):
            raise self._refuse(state)
        if state == RunLifecycle.SUCCESS:
            return self._terminate(state, **updates)
        if {"commit_result", "terminal_status"} & set(updates):
            raise IllegalRunTransitionError("a stage marker cannot set commit_result/terminal_status")
        return self._advance(state, **updates)

    def _terminate(self, state: RunLifecycle, **updates: Any) -> "RunRecord":
        derived = summarize_commit_result(self.commits)
        explicit = updates.pop("commit_result", None)
        terminal_status = updates.pop("terminal_status", None) or state.value
        if state == RunLifecycle.SUCCESS:
            # Stage markers may move the record past COMMITTED (a later
            # milestone that changed nothing); what matters is that the LAST
            # cycle committed, which the derived summary already proves.
            if self.unsettled_commits or derived not in (COMMIT_COMMITTED, COMMIT_NONE):
                raise self._refuse(state, f"commit result is {derived}")
            # Without any commit cycle the caller may say precisely why
            # (e.g. a direct tool write); it can never claim COMMITTED.
            result = explicit if (explicit and not self.commits) else derived
            if result == COMMIT_COMMITTED and not self.commits:
                raise self._refuse(state, "COMMITTED claimed without a commit cycle")
        elif state == RunLifecycle.FAILURE and self.commit_state_unknown:
            # An unsettled/uncertain commit can never be downgraded to failure.
            raise self._refuse(state, "a commit cycle is unsettled or uncertain")
        elif state == RunLifecycle.UNCERTAIN:
            result = COMMIT_UNCERTAIN
        else:
            result = derived if self.commits else (explicit or COMMIT_NOT_COMMITTED)
        if state == RunLifecycle.UNCERTAIN:
            commits = [
                cycle if cycle.get("result") is not None else {**cycle, "result": COMMIT_UNCERTAIN}
                for cycle in self.commits
            ]
            updates["commits"] = commits
        return self._advance(state, commit_result=result, terminal_status=terminal_status, **updates)

    def annotate(self, **updates: Any) -> "RunRecord":
        """Record evidence references without changing lifecycle state."""
        if self.terminal:
            raise IllegalRunTransitionError("cannot annotate a terminal record")
        unknown = set(updates) - _ANNOTATABLE_FIELDS
        if unknown:
            raise IllegalRunTransitionError(f"annotate cannot set {sorted(unknown)}")
        return replace(self, revision=self.revision + 1, updated_at=_now(), **updates)

    def begin_commit(
        self, transaction_id: str, *, intent: str, candidate_hash: Optional[str], **evidence: Any,
    ) -> "RunRecord":
        """Durable commit intent: must precede the first workspace byte."""
        if self.unsettled_commits:
            raise self._refuse(RunLifecycle.COMMIT_ELIGIBLE, "a previous commit is unsettled")
        if self.terminal or self.lifecycle_state not in _COMMIT_SOURCES:
            raise self._refuse(RunLifecycle.COMMIT_ELIGIBLE)
        if not transaction_id or not intent:
            raise self._refuse(RunLifecycle.COMMIT_ELIGIBLE, "intent and transaction id are required")
        if any(cycle["transaction_id"] == transaction_id for cycle in self.commits):
            raise self._refuse(RunLifecycle.COMMIT_ELIGIBLE, f"transaction {transaction_id!r} reused")
        unknown = set(evidence) - _ANNOTATABLE_FIELDS
        if unknown:
            raise IllegalRunTransitionError(f"begin_commit cannot set {sorted(unknown)}")
        cycle = {
            "transaction_id": transaction_id, "intent": intent,
            "candidate_hash": candidate_hash, "result": None,
        }
        if self.active_work_unit is not None:
            cycle["work_unit"] = dict(self.active_work_unit)
        return self._advance(
            RunLifecycle.COMMIT_ELIGIBLE,
            commits=[*self.commits, cycle], commit_intent=intent,
            commit_transaction_id=transaction_id, commit_result=None,
            candidate_hash=candidate_hash, **evidence,
        )

    def settle_commit(self, result: str) -> "RunRecord":
        """Settle the current cycle. COMMITTED -> COMMITTED; a cycle that left
        the workspace unchanged returns to CANDIDATE (a caller may retry);
        UNCERTAIN is terminal."""
        if self.lifecycle_state != RunLifecycle.COMMIT_ELIGIBLE or not self.unsettled_commits:
            raise IllegalRunTransitionError(
                f"no unsettled commit to settle in {self.lifecycle_state.value}"
            )
        if result not in _CYCLE_RESULTS:
            raise IllegalRunTransitionError(f"unknown commit result {result!r}")
        commits = [*self.commits[:-1], {**self.commits[-1], "result": result}]
        if result == COMMIT_UNCERTAIN:
            return replace(self, commits=commits)._terminate(RunLifecycle.UNCERTAIN)
        next_state = RunLifecycle.COMMITTED if result == COMMIT_COMMITTED else RunLifecycle.CANDIDATE
        return self._advance(next_state, commits=commits, commit_result=result)

    @property
    def open_commit_cycles(self) -> Tuple[Dict[str, Any], ...]:
        """Cycles whose result recovery must prove: unsettled or UNCERTAIN."""
        return tuple(
            cycle for cycle in self.commits
            if cycle.get("result") is None or cycle.get("result") == COMMIT_UNCERTAIN
        )

    @property
    def needs_recovery(self) -> bool:
        return (not self.terminal) or self.lifecycle_state in _RECOVERABLE_TERMINALS

    def recover(
        self, cycle_results: Mapping[str, str], provenance: Mapping[str, Any],
    ) -> "RunRecord":
        """Settle a crashed or UNCERTAIN run from proven evidence.

        ``cycle_results`` must give every open cycle (unsettled or UNCERTAIN)
        a result the caller PROVED - COMMITTED, ROLLED_BACK or NOT_COMMITTED,
        never UNCERTAIN - and nothing else; settled cycles never change. The
        caller must hold the workspace lock (only then is a non-terminal
        record provably dead). Never produces SUCCESS."""
        if not self.needs_recovery:
            raise IllegalRunTransitionError(
                f"record {self.run_id!r} is {self.lifecycle_state.value}; nothing to recover"
            )
        open_ids = [cycle["transaction_id"] for cycle in self.open_commit_cycles]
        if sorted(cycle_results) != sorted(open_ids):
            raise IllegalRunTransitionError(
                f"recovery must settle exactly the open cycles {sorted(open_ids)}, "
                f"got {sorted(cycle_results)}"
            )
        proven = {COMMIT_COMMITTED, COMMIT_ROLLED_BACK, COMMIT_NOT_COMMITTED}
        unproven = {txid: result for txid, result in cycle_results.items() if result not in proven}
        if unproven:
            raise IllegalRunTransitionError(f"recovery results must be proven, got {unproven}")
        commits = [
            {**cycle, "result": cycle_results[cycle["transaction_id"]]}
            if cycle["transaction_id"] in cycle_results else cycle
            for cycle in self.commits
        ]
        # The workspace changed (post-commit steps never ran), or the run was
        # UNCERTAIN for a reason no commit cycle accounts for: a human looks.
        needs_review = any(cycle.get("result") == COMMIT_COMMITTED for cycle in commits) or (
            self.lifecycle_state == RunLifecycle.UNCERTAIN and not open_ids
        )
        recovery = {
            **dict(provenance),
            "prior_lifecycle_state": self.lifecycle_state.value,
            "prior_commit_result": self.commit_result,
            "prior_terminal_status": self.terminal_status,
            "settled_cycles": dict(cycle_results),
        }
        return self._advance(
            RunLifecycle.RECOVERED, commits=commits,
            commit_result=summarize_commit_result(commits),
            terminal_status=TERMINAL_STATUS_NEEDS_REVIEW if needs_review else TERMINAL_STATUS_FAILURE,
            recovery=recovery,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["lifecycle_state"] = self.lifecycle_state.value
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RunRecord":
        """Strict load: v1 migrates deterministically, anything else raises
        UnsupportedRunRecordError so callers can fail closed."""
        if not isinstance(payload, dict):
            raise UnsupportedRunRecordError("RunRecord payload is not an object")
        data = dict(payload)
        data.pop("_workspace", None)
        version = data.get("schema_version")
        if version == 1:
            data = _migrate_v1(data)
        if data.get("schema_version") == 2:
            # v2 -> v3 only adds the optional recovery field.
            data["schema_version"] = RUN_RECORD_SCHEMA_VERSION
        elif data.get("schema_version") != RUN_RECORD_SCHEMA_VERSION:
            raise UnsupportedRunRecordError(f"unsupported RunRecord schema_version {version!r}")
        known = {item.name for item in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise UnsupportedRunRecordError(f"unknown RunRecord fields {sorted(unknown)}")
        try:
            data["lifecycle_state"] = RunLifecycle(data["lifecycle_state"])
            record = cls(**data)
        except (KeyError, TypeError, ValueError) as error:
            raise UnsupportedRunRecordError(f"malformed RunRecord: {error}") from error
        if not isinstance(record.revision, int) or record.revision < 1:
            raise UnsupportedRunRecordError(f"malformed RunRecord revision {record.revision!r}")
        return record


def _migrate_v1(data: Dict[str, Any]) -> Dict[str, Any]:
    """v1 -> v2: v1 had one implicit commit cycle and two unused fields."""
    migrated = dict(data)
    migrated.pop("store_revisions", None)
    migrated.pop("candidate_revision", None)
    commits: List[Dict[str, Any]] = []
    result = migrated.get("commit_result")
    if result == "NO_CHANGES":
        # v1 wrote a fabricated intent+NO_CHANGES cycle when nothing changed.
        migrated["commit_intent"] = None
        migrated["commit_result"] = COMMIT_NONE
    elif migrated.get("commit_intent"):
        commits.append({
            "transaction_id": migrated.get("commit_transaction_id") or migrated.get("run_id"),
            "intent": migrated["commit_intent"],
            "candidate_hash": migrated.get("candidate_hash"),
            "result": result,
        })
    migrated["commits"] = commits
    migrated["schema_version"] = 2
    return migrated

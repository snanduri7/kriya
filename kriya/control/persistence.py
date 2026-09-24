"""Control-plane persistence - MA5.1 of the control-plane implementation
plan, extended in MA5.2+ as each store (contracts, artifacts, decisions)
lands. Every store lives at its own path under `.kriya/control/` inside
the target workspace; `_save_json_document`/`_load_json_document` below
are the one shared, reused implementation of "atomic write through
AuthorizedFileWriter, fail-closed read" every store's own save()/load()
delegates to - kept here rather than duplicated per store, per the package
structure this task was scoped from (persistence.py as one shared file,
not one per store).

Every write goes through kriya/policy/filesystem.py's AuthorizedFileWriter
(MA4.16) - the same real containment-and-sensitive-path enforcement every
other authorized workspace write already goes through. No new direct
write bypass is introduced here, per MA5's own explicit constraint.

No separate backup/recovery mechanism exists here because none exists
anywhere else in Kriya's persistence today (kriya/workflow/checkpoint.py's
save_checkpoint, kriya/workflow/milestones.py's MilestoneRunState save -
both plain atomic-write-only, no .bak file) - nothing to mirror. Atomicity
itself (via AuthorizedFileWriter -> edit_safety.py's commit_revision_
grounded_file -> atomic_write_file) is what protects against a partial
write; that is the existing pattern this follows.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from kriya.control.artifacts import ArtifactRegistry
from kriya.control.contracts import ContractRegistry
from kriya.control.state import ControlState
from kriya.control.run_record import RunRecord
from kriya.control.workspace_identity import WorkspaceOwnershipError, ownership_metadata, validate_ownership
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow.edit_safety import content_revision, read_file_revision

logger = logging.getLogger(__name__)

_CONTROL_DIR = os.path.join(".kriya", "control")
_STATE_FILENAME = "state.json"
_CONTRACTS_FILENAME = "contracts.json"
_ARTIFACTS_FILENAME = "artifacts.json"
_DECISIONS_FILENAME = "decisions.jsonl"
_APPROVED_PLANS_DIRNAME = "plans"
_RUNS_DIRNAME = "runs"


class StaleRunRecordError(RuntimeError):
    """A writer attempted to replace a newer durable RunRecord revision."""


def _control_dir(workspace_path: str) -> str:
    return os.path.join(workspace_path, _CONTROL_DIR)


def control_state_path(workspace_path: str) -> str:
    return os.path.join(_control_dir(workspace_path), _STATE_FILENAME)


def contract_registry_path(workspace_path: str) -> str:
    return os.path.join(_control_dir(workspace_path), _CONTRACTS_FILENAME)


def artifact_registry_path(workspace_path: str) -> str:
    return os.path.join(_control_dir(workspace_path), _ARTIFACTS_FILENAME)


def decision_ledger_path(workspace_path: str) -> str:
    return os.path.join(_control_dir(workspace_path), _DECISIONS_FILENAME)


def approved_plan_path(workspace_path: str, plan_id: str) -> str:
    """Owned durable path for one validated authoritative plan."""
    safe_plan_id = "".join(ch for ch in plan_id if ch.isalnum() or ch in {"-", "_"})
    if not safe_plan_id or safe_plan_id != plan_id:
        raise ValueError("plan_id must contain only letters, digits, '-' or '_'")
    return os.path.join(_control_dir(workspace_path), _APPROVED_PLANS_DIRNAME, f"{safe_plan_id}.json")


_RUN_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id) and set(run_id) <= _RUN_ID_CHARS


def run_record_path(workspace_path: str, run_id: str) -> str:
    if not _valid_run_id(run_id):
        raise ValueError("run_id must contain only letters, digits, '-' or '_'")
    return os.path.join(_control_dir(workspace_path), _RUNS_DIRNAME, f"{run_id}.json")


class UnreadableRunRecordError(RuntimeError):
    """A run record exists but cannot be trusted (corrupt, unknown schema,
    or owned by another workspace). Never treated as absent: a record that
    cannot be read may be the only evidence of an interrupted commit."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"unreadable RunRecord {path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class RunRecordScan:
    records: List[RunRecord]
    unreadable: List[UnreadableRunRecordError]


def _read_run_record(workspace_path: str, run_id: str) -> Tuple[Optional[RunRecord], Optional[str]]:
    """(record, content revision) from ONE read; (None, None) when absent."""
    path = run_record_path(workspace_path, run_id)
    try:
        # Same decoding as read_file_revision(), so the content revision the
        # revision-grounded write checks is identical to this read.
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None, None
    except OSError as error:
        raise UnreadableRunRecordError(path, f"{type(error).__name__}: {error}") from error
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("RunRecord document is not a JSON object")
        validate_ownership(workspace_path, payload, path)
        record = RunRecord.from_dict(payload)
    except (ValueError, WorkspaceOwnershipError) as error:
        raise UnreadableRunRecordError(path, f"{type(error).__name__}: {error}") from error
    if record.run_id != run_id:
        raise UnreadableRunRecordError(path, f"file names run {run_id!r} but holds {record.run_id!r}")
    return record, content_revision(text)


def load_run_record(workspace_path: str, run_id: str) -> Optional[RunRecord]:
    """None only when no record exists; raises UnreadableRunRecordError when
    one exists but cannot be trusted."""
    return _read_run_record(workspace_path, run_id)[0]


def scan_run_records(workspace_path: str) -> RunRecordScan:
    """Every record under runs/, with untrustworthy ones reported, not hidden.

    Files whose names are not run ids (editor backups, stray notes, temp
    files) are not run records and are ignored."""
    directory = os.path.join(_control_dir(workspace_path), _RUNS_DIRNAME)
    if not os.path.isdir(directory):
        return RunRecordScan([], [])
    records: List[RunRecord] = []
    unreadable: List[UnreadableRunRecordError] = []
    for name in sorted(os.listdir(directory)):
        run_id = name[:-5] if name.endswith(".json") else ""
        if not _valid_run_id(run_id):
            continue
        try:
            record = load_run_record(workspace_path, run_id)
        except UnreadableRunRecordError as error:
            unreadable.append(error)
            continue
        if record is not None:
            records.append(record)
    return RunRecordScan(records, unreadable)


def list_run_records(workspace_path: str) -> List[RunRecord]:
    """Readable records only - callers deciding safety must use
    scan_run_records() so unreadable records fail closed."""
    return scan_run_records(workspace_path).records


def save_run_record(
    workspace_path: str, record: RunRecord, *, expected_revision: Optional[int]
) -> None:
    """Atomically persist a record after an optimistic revision check.

    The expected record revision and the file's content revision come from
    the same read, and the write is revision-grounded on that content, so a
    writer that changed the record since that read is detected as a
    FileRevisionConflict. The revision-grounded write itself re-reads and
    then replaces, so the cross-process guarantee rests on the workspace run
    lock (only the owning run writes its record), not on this check alone."""
    current, file_revision = _read_run_record(workspace_path, record.run_id)
    actual_revision = current.revision if current is not None else None
    if actual_revision != expected_revision:
        raise StaleRunRecordError(
            f"RunRecord {record.run_id!r} expected revision {expected_revision!r}, "
            f"found {actual_revision!r}"
        )
    expected_next = 1 if expected_revision is None else expected_revision + 1
    if record.revision != expected_next:
        raise StaleRunRecordError(
            f"RunRecord {record.run_id!r} must write revision {expected_next}, "
            f"got {record.revision}"
        )
    _save_json_document(
        workspace_path, run_record_path(workspace_path, record.run_id),
        record.to_dict(), derived_from_active_run=False,
        expected_file_revision=file_revision if file_revision is not None else content_revision(""),
    )


def save_approved_plan(workspace_path: str, plan_id: str, payload: Dict[str, Any]) -> None:
    """Atomically persist a validated plan and its execution-stage state."""
    _save_json_document(workspace_path, approved_plan_path(workspace_path, plan_id), payload)


def load_approved_plan(workspace_path: str, plan_id: str) -> Optional[Dict[str, Any]]:
    """Load a validated plan document, enforcing workspace ownership."""
    return _load_json_document(approved_plan_path(workspace_path, plan_id), workspace_path)


def _save_json_document(
    workspace_path: str, path: str, payload: Dict[str, Any], *,
    derived_from_active_run: bool = True, expected_file_revision: Optional[str] = None,
) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    owned_payload = dict(payload)
    owned_payload["_workspace"] = ownership_metadata(workspace_path)
    if derived_from_active_run:
        from kriya.control.run_coordinator import current_run_context
        context = current_run_context()
        if context is not None and context.record_revision is not None:
            owned_payload["_run_record"] = {
                "classification": "derived",
                "run_id": context.run_id,
                "revision": context.record_revision,
            }
    content = json.dumps(owned_payload, indent=2, sort_keys=True)
    expected_revision = (
        expected_file_revision if expected_file_revision is not None else read_file_revision(path)
    )
    AuthorizedFileWriter(workspace_path).commit_file(path, content, expected_revision=expected_revision)


def _load_json_document(path: str, workspace_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """None if the file has never been saved, or if it exists but can't be
    parsed (fails closed - a corrupt store file must never crash the
    caller or silently be treated as an empty-but-valid store)."""

    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
            if workspace_path is not None:
                validate_ownership(workspace_path, payload, path)
            return payload
    except WorkspaceOwnershipError:
        raise
    except Exception:
        logger.warning("Failed to load control-plane document at %s - treating as absent", path, exc_info=True)
        return None


def save_control_state(workspace_path: str, state: ControlState) -> None:
    # Pure save - persists exactly the ControlState it is given, never
    # silently mutates a field first (a real caller-visible round-trip
    # invariant several existing tests rely on directly: save(x); reload();
    # assert reloaded == x). STATE-001's own workspace_content_hash
    # freshness requirement is the CALLER's responsibility - see
    # kriya/workflow/workflow_controller.py's own _save_control_state_
    # with_fresh_content_hash() for the one real caller that actually needs
    # per-save freshness (structured/enforce-mode's per-subtask loop,
    # where real content changes between saves within the SAME run) -
    # deliberately NOT centralized here, where it would silently diverge
    # what every OTHER caller persists from what it explicitly passed in.
    _save_json_document(workspace_path, control_state_path(workspace_path), state.to_dict())


def load_control_state(workspace_path: str) -> Optional[ControlState]:
    data = _load_json_document(control_state_path(workspace_path), workspace_path)
    if data is None:
        return None
    try:
        return ControlState.from_dict(data)
    except Exception:
        logger.warning("Failed to reconstruct ControlState from %s - treating as absent", control_state_path(workspace_path), exc_info=True)
        return None


def save_contract_registry(workspace_path: str, registry: ContractRegistry) -> None:
    _save_json_document(workspace_path, contract_registry_path(workspace_path), registry.to_dict())


def load_contract_registry(workspace_path: str) -> ContractRegistry:
    """Never returns None - an empty ContractRegistry (nothing registered
    yet) is a perfectly valid starting state, unlike ControlState which has
    a meaningful 'never initialized' None. A corrupt file still fails
    closed to empty, logged, exactly like _load_json_document's own
    contract."""

    data = _load_json_document(contract_registry_path(workspace_path), workspace_path)
    if data is None:
        return ContractRegistry()
    data.pop("_run_record", None)
    try:
        return ContractRegistry.from_dict(data)
    except Exception:
        logger.warning("Failed to reconstruct ContractRegistry from %s - starting empty", contract_registry_path(workspace_path), exc_info=True)
        return ContractRegistry()


def save_artifact_registry(workspace_path: str, registry: ArtifactRegistry) -> None:
    _save_json_document(workspace_path, artifact_registry_path(workspace_path), registry.to_dict())


def load_artifact_registry(workspace_path: str) -> ArtifactRegistry:
    data = _load_json_document(artifact_registry_path(workspace_path), workspace_path)
    if data is None:
        return ArtifactRegistry()
    data.pop("_run_record", None)
    try:
        return ArtifactRegistry.from_dict(data)
    except Exception:
        logger.warning("Failed to reconstruct ArtifactRegistry from %s - starting empty", artifact_registry_path(workspace_path), exc_info=True)
        return ArtifactRegistry()

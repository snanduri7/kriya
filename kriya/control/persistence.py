"""Control-plane persistence - MA5.1 of the control-plane implementation
plan, extended in MA5.2+ as each store (contracts, artifacts, decisions)
lands. Every store lives at its own path under `.kriya/control/` inside
the target workspace; `_save_json_document`/`_load_json_document` below
are the one shared, reused implementation of "atomic write through
the control-store writer, fail-closed read" every store's own save()/load()
delegates to - kept here rather than duplicated per store, per the package
structure this task was scoped from (persistence.py as one shared file,
not one per store).

Every write goes through kriya/control/control_store.py's
write_control_file (PLAT-039): Kriya's own control state is written only
beneath `.kriya/`, and candidate write authority (AuthorizedFileWriter)
refuses every trusted control path, so the two never overlap.

No separate backup/recovery mechanism exists here because none exists
anywhere else in Kriya's persistence today (kriya/workflow/checkpoint.py's
save_checkpoint, kriya/workflow/milestones.py's MilestoneRunState save -
both plain atomic-write-only, no .bak file) - nothing to mirror. Atomicity
itself (via write_control_file -> edit_safety.py's commit_revision_
grounded_file -> atomic_write_file) is what protects against a partial
write; that is the existing pattern this follows.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from kriya.context.workspace_identity import (
    WorkspaceOwnershipError,
    authority_root,
    ensure_existing_document_owned,
    ownership_metadata,
    validate_ownership,
)
from kriya.control.artifacts import ArtifactRegistry
from kriya.control.contracts import ContractRegistry, ContractRegistryCorruptError, ContractRegistryTransitionError
from kriya.control.control_store import write_control_file
from kriya.control.human_decision_store import human_decision_path, write_document
from kriya.control.run_record import RunRecord
from kriya.control.state import ControlState
from kriya.foundation.ids import CONTROL_ID_CHARS, is_control_id
from kriya.repository.commit import content_revision, read_file_revision
from kriya.repository.snapshot import raw_digest

logger = logging.getLogger(__name__)

_CONTROL_DIR = os.path.join(".kriya", "control")
_STATE_FILENAME = "state.json"
_CONTRACTS_FILENAME = "contracts.json"
_CONTRACTS_PENDING_DIRNAME = "contracts.pending"
_ARTIFACTS_FILENAME = "artifacts.json"
_DECISIONS_FILENAME = "decisions.jsonl"
_APPROVED_PLANS_DIRNAME = "plans"
_RUNS_DIRNAME = "runs"


class StaleRunRecordError(RuntimeError):
    """A writer attempted to replace a newer durable RunRecord revision."""


def _control_dir(workspace_path: str) -> str:
    """D-001: the control plane (RunRecords, launches, decisions, contracts, artifacts, plans) belongs to the workspace's AUTHORITY root - its
    repository - so ``<repo>`` and ``<repo>/sub`` read and write the same stores, never parallel ones."""
    return os.path.join(authority_root(workspace_path), _CONTROL_DIR)


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


# The control-plane id rule lives in FOUNDATION since D3 (CONFIG's run-log path needs it too, without importing STATE).
_RUN_ID_CHARS = CONTROL_ID_CHARS
_valid_run_id = is_control_id


def run_record_path(workspace_path: str, run_id: str) -> str:
    if not _valid_run_id(run_id):
        raise ValueError("run_id must contain only letters, digits, '-' or '_'")
    return os.path.join(_control_dir(workspace_path), _RUNS_DIRNAME, f"{run_id}.json")


def launch_record_path(workspace_path: str, launch_id: str) -> str:
    """GUI M6: the durable launch record of one desktop-started run (kriya/control/launch.py). D2 M1a: it lives in the operator's authority home
    (kriya/control/human_decision_store.py), not in the workspace; the old ``<workspace>/.kriya/control/launches/`` location is never read."""
    if not _valid_run_id(launch_id):
        raise ValueError("launch_id must contain only letters, digits, '-' or '_'")
    return human_decision_path(workspace_path, f"{launch_id}.json")


def save_launch_record(workspace_path: str, launch_id: str, payload: Dict[str, Any]) -> None:
    """Atomically (re)write one launch record; ownership-stamped like every control document, and never over an unstamped or foreign one."""
    write_document(workspace_path, launch_record_path(workspace_path, launch_id), payload)


def launch_review_decision_path(workspace_path: str, launch_id: str, review_id: str) -> str:
    """GUI final: the ONE durable decision of ONE pending change review, beside the launch record. One file per review id: a decision for a review
    that has been superseded can never be read as the answer to the current one."""
    if not _valid_run_id(launch_id) or not _valid_run_id(review_id):
        raise ValueError("launch_id and review_id must contain only letters, digits, '-' or '_'")
    return human_decision_path(workspace_path, f"{launch_id}.review-{review_id}.json")


def save_launch_review_decision(workspace_path: str, launch_id: str, review_id: str, payload: Dict[str, Any]) -> None:
    """Writes the decision ONLY if none exists yet, ATOMICALLY: the complete, ownership-stamped document is staged beside the final name and then hard-linked to it
    (`os.link` fails with FileExistsError when the final name exists, whoever created it and however concurrently), so of any number of simultaneous writers exactly
    one succeeds and nobody ever overwrites a decision. A reader never sees a partial decision: the final name only ever appears complete."""
    write_document(workspace_path, launch_review_decision_path(workspace_path, launch_id, review_id), payload, exclusive=True)


def launch_review_diff_path(workspace_path: str, launch_id: str, review_id: str) -> str:
    """PRODUCT-UX-COMPLETION: the EXACT diff text of ONE change review, beside the launch record. Approval-scoped: it holds only what the person is asked to approve
    (the unified diffs the gate presented), is bound by digest to the pending review, and is served only by `change_approval.review` after re-verification. It is
    not part of the evidence store and does not widen ``evidence.attempt_recorder.content_visibility``."""
    if not _valid_run_id(launch_id) or not _valid_run_id(review_id):
        raise ValueError("launch_id and review_id must contain only letters, digits, '-' or '_'")
    return human_decision_path(workspace_path, f"{launch_id}.review-diff-{review_id}.json")


def save_launch_review_diff(workspace_path: str, launch_id: str, review_id: str, payload: Dict[str, Any]) -> None:
    """Atomically writes the ownership-stamped diff document of one review (the same control writer as every control document)."""
    write_document(workspace_path, launch_review_diff_path(workspace_path, launch_id, review_id), payload)


def load_launch_review_diff(workspace_path: str, launch_id: str, review_id: str) -> Optional[Dict[str, Any]]:
    """None when no complete, ownership-stamped diff document exists for the review (missing, partial, corrupt or foreign): never partial content."""
    path = launch_review_diff_path(workspace_path, launch_id, review_id)
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or "_workspace" not in payload:
        return None
    try:
        validate_ownership(workspace_path, payload, path)
    except WorkspaceOwnershipError:
        return None
    return payload


def load_launch_record(workspace_path: str, launch_id: str) -> Optional[Dict[str, Any]]:
    """None when no record exists; raises UnreadableRunRecordError when one exists but is not trustworthy (corrupt or owned by another workspace)."""
    path = launch_record_path(workspace_path, launch_id)
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise UnreadableRunRecordError(path, f"{type(error).__name__}: {error}") from error
    if not isinstance(payload, dict):
        raise UnreadableRunRecordError(path, "launch record is not a JSON object")
    try:
        validate_ownership(workspace_path, payload, path)
    except WorkspaceOwnershipError as error:
        raise UnreadableRunRecordError(path, f"{type(error).__name__}: {error}", reason_code=error.reason_code) from error
    return payload


class UnreadableRunRecordError(RuntimeError):
    """A run record exists but cannot be trusted (corrupt, unknown schema,
    or owned by another workspace). Never treated as absent: a record that
    cannot be read may be the only evidence of an interrupted commit."""

    def __init__(self, path: str, reason: str, *, reason_code: Optional[str] = None) -> None:
        super().__init__(f"unreadable RunRecord {path}: {reason}")
        self.path = path
        self.reason = reason
        # D2 M4: set only when the record was refused for its ownership stamp (WORKSPACE_OWNERSHIP_MISSING / _MISMATCH), so the public
        # refusal can say so instead of a generic "unreadable".
        self.reason_code = reason_code


@dataclass(frozen=True)
class RunRecordScan:
    records: List[RunRecord]
    unreadable: List[UnreadableRunRecordError]


def _read_run_record(workspace_path: str, run_id: str) -> Tuple[Optional[RunRecord], Optional[str]]:
    """(record, content revision) from ONE read; (None, None) when absent."""
    path = run_record_path(workspace_path, run_id)
    try:
        # FILE-INTEGRITY-CONTRACT-001: the revision is the raw bytes' digest,
        # exactly what read_file_revision() and the grounded write check.
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None, None
    except OSError as error:
        raise UnreadableRunRecordError(path, f"{type(error).__name__}: {error}") from error
    try:
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("RunRecord document is not a JSON object")
        validate_ownership(workspace_path, payload, path)
        record = RunRecord.from_dict(payload)
    except (ValueError, WorkspaceOwnershipError) as error:
        raise UnreadableRunRecordError(
            path, f"{type(error).__name__}: {error}",
            reason_code=error.reason_code if isinstance(error, WorkspaceOwnershipError) else None,
        ) from error
    if record.run_id != run_id:
        raise UnreadableRunRecordError(path, f"file names run {run_id!r} but holds {record.run_id!r}")
    return record, raw_digest(data)


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


_LEGACY_SCAN_PRUNED = frozenset({".git", ".kriya", "node_modules", "__pycache__", ".venv", "venv", ".tox", "target", "build", "dist", ".gradle", ".idea"})
LEGACY_SCAN_DIRECTORY_BUDGET = 20000


def nested_control_state(workspace_path: str) -> Tuple[Tuple[Tuple[str, str], ...], bool]:
    """D-001: control state a build from BEFORE the authority-root rule left under a directory BELOW the workspace authority's root
    (``<repo>/sub/.kriya/control``, written when ``<repo>/sub`` was itself the workspace). Those stores are no longer the workspace's, so an
    unsettled commit or run record in one would make the repository look CLEAN again. Returns ((directory, reason), ...) for every such directory that
    holds an UNSETTLED/uncertain record or one that cannot be read (fail closed), and whether the whole tree was examined (a nested repository is
    its own authority and is not entered; the walk is bounded by LEGACY_SCAN_DIRECTORY_BUDGET directories)."""
    from kriya.repository.commit import CommitState, load_commit_evidence

    root = authority_root(workspace_path)
    found: List[Tuple[str, str]] = []
    visited = 0
    complete = True
    for directory, dirnames, _filenames in os.walk(root):
        visited += 1
        if visited > LEGACY_SCAN_DIRECTORY_BUDGET:
            complete = False
            break
        dirnames[:] = sorted(name for name in dirnames if name not in _LEGACY_SCAN_PRUNED
                             and not os.path.lexists(os.path.join(directory, name, ".git")))
        if directory == root:
            continue
        control = os.path.join(directory, _CONTROL_DIR)
        reasons: List[str] = []
        runs_dir, commits_dir = os.path.join(control, _RUNS_DIRNAME), os.path.join(control, "commits")
        for name in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(runs_dir, name), "rb") as handle:
                    record = RunRecord.from_dict(json.loads(handle.read().decode("utf-8")))
            except (OSError, ValueError, KeyError, TypeError) as error:
                reasons.append(f"{name}: unreadable ({type(error).__name__})")
                continue
            if record.commit_state_unknown:
                reasons.append(f"{name}: unsettled commit state")
        for name in sorted(os.listdir(commits_dir)) if os.path.isdir(commits_dir) else []:
            if not name.endswith(".json"):
                continue
            try:
                evidence = load_commit_evidence(os.path.join(commits_dir, name), workspace_path=workspace_path)
            except Exception as error:  # pylint: disable=broad-except
                reasons.append(f"{name}: unreadable ({type(error).__name__})")
                continue
            if evidence.state in (CommitState.IN_PROGRESS, CommitState.UNCERTAIN):
                reasons.append(f"{name}: commit {evidence.state.value}")
        if reasons:
            found.append((directory, "; ".join(reasons)))
    return tuple(found), complete


def list_run_records(workspace_path: str) -> List[RunRecord]:
    """Readable records only - callers deciding safety must use
    scan_run_records() so unreadable records fail closed."""
    return scan_run_records(workspace_path).records


def save_run_record(
    workspace_path: str, record: RunRecord, *, expected_revision: Optional[int], durable: bool = False
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
        expected_file_revision=file_revision if file_revision is not None else content_revision(""), durable=durable,
    )


def save_approved_plan(workspace_path: str, plan_id: str, payload: Dict[str, Any]) -> None:
    """Atomically persist a validated plan and its execution-stage state."""
    _save_json_document(workspace_path, approved_plan_path(workspace_path, plan_id), payload)


def load_approved_plan(workspace_path: str, plan_id: str) -> Optional[Dict[str, Any]]:
    """Load a validated plan document, enforcing workspace ownership."""
    return _load_json_document(approved_plan_path(workspace_path, plan_id), workspace_path)


def _read_current_bytes(path: str) -> bytes:
    """The raw bytes at ``path`` (empty when absent): one read serves the revision and the ownership check."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return b""


def _save_json_document(
    workspace_path: str, path: str, payload: Dict[str, Any], *,
    derived_from_active_run: bool = True, expected_file_revision: Optional[str] = None, durable: bool = False,
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
    if expected_file_revision is not None:
        expected_revision = expected_file_revision  # the caller read (and ownership-validated) the current document itself
    else:
        current = _read_current_bytes(path)
        ensure_existing_document_owned(workspace_path, current, path)
        expected_revision = raw_digest(current)
    write_control_file(workspace_path, path, content, expected_revision=expected_revision, durable=durable)


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


class ControlStateUnreadableError(RuntimeError):
    """D1: the persisted ControlState exists but cannot be read. Unresolved execution truth (a TOOL STARTED record) may live in it, so it is NEVER read as
    "absent": nothing resumes from it, nothing starts fresh over it, and the bytes are left exactly as they are."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"the persisted control state {path!r} cannot be read ({reason}); it was left untouched. Kriya will neither resume from it nor "
                         "start fresh over it: inspect it, then move it aside yourself.")
        self.path = path
        self.reason = reason


def _read_control_state_strict(workspace_path: str) -> Optional[ControlState]:
    """None only when the file has NEVER been saved; an existing file that cannot be parsed or reconstructed raises ControlStateUnreadableError."""
    path = control_state_path(workspace_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        validate_ownership(workspace_path, data, path)
        return ControlState.from_dict(data)
    except WorkspaceOwnershipError:
        raise
    except Exception as error:  # pylint: disable=broad-except
        raise ControlStateUnreadableError(path, f"{type(error).__name__}: {error}") from error


def inspect_control_state(workspace_path: str) -> Tuple[Optional[ControlState], Optional[Tuple[str, str]]]:
    """Read-only: (the readable ControlState or None, (path, reason) of an existing control state that cannot be read or None). Never raises for those two outcomes."""
    try:
        return _read_control_state_strict(workspace_path), None
    except ControlStateUnreadableError as error:
        return None, (error.path, error.reason)
    except WorkspaceOwnershipError:
        return None, None  # the ownership gates own that refusal


def control_state_unreadable(workspace_path: str) -> Optional[Tuple[str, str]]:
    """(path, reason) of an existing control state that cannot be read, else None. Read-only."""
    return inspect_control_state(workspace_path)[1]


def save_control_state(workspace_path: str, state: ControlState, *, durable: bool = False) -> None:
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
    _read_control_state_strict(workspace_path)  # D1: an existing UNREADABLE state is never overwritten (raises ControlStateUnreadableError); its bytes are the evidence
    # durable=True (D1): an authoritative TOOL lifecycle transition - fsync-grade, every step required (kriya/repository/atomic_write.py).
    _save_json_document(workspace_path, control_state_path(workspace_path), state.to_dict(), durable=durable)


def load_control_state(workspace_path: str) -> Optional[ControlState]:
    """None only when no control state was ever saved. D1: an existing but unreadable state raises ControlStateUnreadableError - it is never "absent"."""
    return _read_control_state_strict(workspace_path)


def load_control_state_run_reference(workspace_path: str) -> Optional[str]:
    """The run id whose RunRecord the persisted ControlState was derived
    under (``_run_record``), or None when there is none. PRD-008: enforce
    resume checks that record, and retention protects it."""
    try:
        data = _load_json_document(control_state_path(workspace_path), workspace_path)
    except WorkspaceOwnershipError:
        return None
    reference = (data or {}).get("_run_record")
    if isinstance(reference, dict) and isinstance(reference.get("run_id"), str):
        return reference["run_id"]
    return None


def save_contract_registry(workspace_path: str, registry: ContractRegistry) -> None:
    _save_json_document(workspace_path, contract_registry_path(workspace_path), registry.to_dict())


def load_contract_registry(workspace_path: str) -> ContractRegistry:
    """Never returns None: a registry that was never saved is a valid, empty
    starting state. PRD-029: a registry file that EXISTS but cannot be read
    as a valid registry raises ContractRegistryCorruptError - it is never
    interpreted as empty (which previously let the next save overwrite it,
    and let resume/commit proceed as though no contracts existed)."""

    path = contract_registry_path(workspace_path)
    if not os.path.isfile(path):
        return ContractRegistry()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise ContractRegistryCorruptError(path, f"unreadable: {error}") from error
    return _registry_from_text(path, text, workspace_path)


def _registry_from_text(path: str, text: str, workspace_path: str) -> ContractRegistry:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ContractRegistryCorruptError(path, f"unreadable: {error}") from error
    if not isinstance(data, dict):
        raise ContractRegistryCorruptError(path, "registry payload is not an object")
    validate_ownership(workspace_path, data, path)
    data = dict(data)
    data.pop("_run_record", None)
    data.pop("_workspace", None)
    try:
        return ContractRegistry.from_dict(data)
    except (KeyError, TypeError, ValueError) as error:
        raise ContractRegistryCorruptError(path, f"invalid registry: {type(error).__name__}: {error}") from error


def pending_contract_registry_path(workspace_path: str, transaction_id: str) -> str:
    """PRD-029: where a commit's after-state registry waits until its source
    bytes are committed. Keyed by the commit transaction id."""
    if not transaction_id or any(ch not in _RUN_ID_CHARS for ch in transaction_id):
        raise ValueError(f"invalid transaction id {transaction_id!r}")
    return os.path.join(_control_dir(workspace_path), _CONTRACTS_PENDING_DIRNAME, f"{transaction_id}.json")


def stage_pending_contract_registry(workspace_path: str, transaction_id: str, payload: Dict[str, Any]) -> str:
    """Write the after-state registry beside the live one - never
    authoritative until promoted."""
    path = pending_contract_registry_path(workspace_path, transaction_id)
    _save_json_document(workspace_path, path, payload)
    return path


def read_pending_contract_registry(
    workspace_path: str, transaction_id: str,
) -> Optional[Tuple[str, ContractRegistry]]:
    """(exact staged text, parsed registry), or None when nothing is staged."""
    path = pending_contract_registry_path(workspace_path, transaction_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return text, _registry_from_text(path, text, workspace_path)


def promote_pending_contract_registry(
    workspace_path: str, transaction_id: str, *, before_digest: str, after_digest: str,
) -> None:
    """Make the staged after-state authoritative - exactly, or not at all.
    The staged payload must be the one the commit intent recorded, and the
    live registry must still be the before-state; the write is a
    revision-checked (compare-and-swap) control-store commit of the
    staged bytes, never a rebuilt object."""
    staged = read_pending_contract_registry(workspace_path, transaction_id)
    if staged is None:
        raise ContractRegistryTransitionError(f"no staged registry for transaction {transaction_id!r}")
    text, registry = staged
    if registry.digest() != after_digest:
        raise ContractRegistryTransitionError("staged registry is not the after-state the commit intent recorded")
    if load_contract_registry(workspace_path).digest() != before_digest:
        raise ContractRegistryTransitionError("the live registry changed since the commit intent was recorded")
    path = contract_registry_path(workspace_path)
    write_control_file(workspace_path, path, text, expected_revision=read_file_revision(path))
    discard_pending_contract_registry(workspace_path, transaction_id)


def discard_pending_contract_registry(workspace_path: str, transaction_id: str) -> bool:
    """Remove a staged transition whose commit did not happen (or that was
    already promoted). Returns whether a file was removed."""
    path = pending_contract_registry_path(workspace_path, transaction_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


# Contract-registry recovery (PRD-029): moved verbatim from kriya/workflow/contract_lifecycle.py in D2 - it reads and settles only
# the registry files this module owns, so recovery (kriya/control/recovery.py) no longer imports the kernel.
# Recovery outcomes.
TRANSITION_PROMOTED = "PROMOTED"
TRANSITION_ALREADY_APPLIED = "ALREADY_APPLIED"
TRANSITION_DISCARDED = "DISCARDED"
TRANSITION_NEEDS_REVIEW = "NEEDS_REVIEW"


def complete_contract_transition(workspace_path: str, transaction_id: str, intent: Mapping[str, Any], *,
                                 committed: bool) -> str:
    """Recovery's half of the transaction, from the cycle's proven result.
    A committed cycle's transition is promoted exactly (or found already
    applied); an uncommitted cycle's staged transition is discarded while
    the live registry must still be the before-state; anything else is
    NEEDS_REVIEW, never forced."""
    try:
        current = load_contract_registry(workspace_path).digest()
    except Exception:
        return TRANSITION_NEEDS_REVIEW
    if committed:
        if current == intent.get("after_digest"):
            discard_pending_contract_registry(workspace_path, transaction_id)
            return TRANSITION_ALREADY_APPLIED
        try:
            promote_pending_contract_registry(
                workspace_path, transaction_id,
                before_digest=intent.get("before_digest"), after_digest=intent.get("after_digest"),
            )
        except (ContractRegistryTransitionError, OSError, ValueError):
            return TRANSITION_NEEDS_REVIEW
        return TRANSITION_PROMOTED
    if current != intent.get("before_digest"):
        return TRANSITION_NEEDS_REVIEW
    discard_pending_contract_registry(workspace_path, transaction_id)
    return TRANSITION_DISCARDED


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

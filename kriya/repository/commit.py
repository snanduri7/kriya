"""REPOSITORY: the revision-grounded commit of staged files with durable crash evidence (full modularization,
slice M1; MODULE-CONTRACTS §11).

Moved verbatim from kriya/workflow/edit_safety.py, which re-exports every public name until P1:
``commit_revision_grounded_file`` (one file, only if its base revision is unchanged), ``commit_revision_grounded_batch``
(the PRD-005 transaction: whole-batch preflight, IN_PROGRESS evidence before any staged byte, fsynced staging,
re-checked apply, rollback, ROLLED_BACK/UNCERTAIN/COMMITTED evidence), the commit-evidence store under
``<workspace>/.kriya/control/commits`` (load/list/settle/state, the uncertainty check) and the candidate identity
(``candidate_digest``) that the RunRecord commit cycle and recovery share. The error classes are this module's
(``CandidateMaterializationError`` included: kriya/workflow/terminal_commit.py re-exports it). Retention of the
evidence is STATE's (kriya/control/retention.py), never here.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kriya.context.workspace_identity import authority_root, ownership_metadata, validate_ownership
from kriya.platform.filesystem_semantics import PathIdentity, PathRelation, fold_name, path_identity, path_relation
from kriya.repository.atomic_write import _atomic_write_bytes, _audit_write_file, _fsync_directory, atomic_write_file
from kriya.repository.snapshot import file_raw_digest, raw_digest


class CandidateMaterializationError(RuntimeError):
    """An approved candidate file is missing or unreadable; nothing was
    committed. Raised by terminal_commit.materialize_candidate, which
    re-exports it; defined here so the terminal gate service can bind a
    candidate without importing the commit module."""


class FileRevisionConflict(ValueError):
    """The file changed after Kriya read it and before the staged write."""


class BatchCommitError(RuntimeError):
    """A staged batch could not be committed or completely rolled back."""


class UncertainCommitError(BatchCommitError):
    """A prior process stopped while a source commit may have been active."""


class CommitState(str, Enum):
    """Durable source-commit states; absence means ``not_started``."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class CommitEvidence:
    schema_version: int
    transaction_id: str
    state: CommitState
    started_at_unix: float
    updated_at_unix: float
    operations: Tuple[Dict[str, Any], ...]
    result_revisions: Dict[str, str]
    failure: Optional[str] = None
    # PRD-008 (schema 2): identity of the exact candidate this transaction
    # applies (candidate_digest), linking it to its RunRecord commit cycle,
    # and the provenance of an explicit `kriya runs recover` settlement.
    candidate_hash: Optional[str] = None
    recovery: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["operations"] = list(self.operations)
        return payload


class BatchCommitResult(dict):
    """Backward-compatible revision mapping with commit evidence attached."""

    def __init__(self, revisions: Dict[str, str], evidence: CommitEvidence) -> None:
        super().__init__(revisions)
        self.evidence = evidence


@dataclass(frozen=True)
class StagedFileWrite:
    """One fully materialized candidate file and the revision it was based on.

    ``base_path`` can differ from ``target_path`` when a sandbox file has not
    been materialized yet and generation read the corresponding workspace file.
    The source revision is still guarded before the candidate is committed.
    ``delete`` represents an approved deletion in the same guarded batch; its
    empty ``content`` value is ignored except for the returned tombstone
    revision.
    """

    target_path: str
    content: str
    base_path: str
    expected_base_revision: str
    delete: bool = False
    # Optional because legacy/sandbox callers predate explicit create/modify
    # identity. Terminal source commits set it, making an empty existing file
    # distinguishable from a missing create target.
    expected_base_exists: Optional[bool] = None
    # PRD-005: the exact verified bytes. When set they are what reaches disk
    # (``content`` stays the decoded text used for revision identity), so a
    # CRLF or non-UTF-8 candidate is committed byte-for-byte.
    content_bytes: Optional[bytes] = None
    # PRD-005: permission bits for the committed file (e.g. an executable
    # ``mvnw`` created by the candidate). None keeps an existing target's
    # mode, or the umask default for a new file.
    mode: Optional[int] = None


def content_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_file_revision(full_path: str) -> str:
    """FILE-INTEGRITY-CONTRACT-001: a file's revision is the SHA-256 of its
    raw bytes (the digest of no bytes when it is absent), never of a decoded
    copy - two different byte sequences never share a revision. For valid
    UTF-8 with LF line endings it equals content_revision() of its text."""
    return file_raw_digest(full_path)


def commit_revision_grounded_file(
    full_path: str, content: str, expected_revision: str,
    workspace_path: Optional[str] = None, *, content_bytes: Optional[bytes] = None,
) -> str:
    """Atomically commit a fully staged file only if its base is unchanged.

    ``content_bytes``, when given, are the exact bytes written (``content``
    is then only their text view); the returned revision is theirs."""
    actual_revision = read_file_revision(full_path)
    if actual_revision != expected_revision:
        raise FileRevisionConflict(
            f"Refusing stale write to '{full_path}': expected revision "
            f"{expected_revision[:12]}, found {actual_revision[:12]}. Re-read the file and retry."
        )
    if os.path.islink(full_path):
        raise FileRevisionConflict(
            f"Refusing write to '{full_path}': it is a symbolic link, never replaced by a regular file."
        )
    data = content_bytes if content_bytes is not None else content.encode("utf-8")
    mode = os.stat(full_path).st_mode & 0o7777 if os.path.exists(full_path) else None
    atomic_write_file(full_path, content, workspace_path=workspace_path, content_bytes=data)
    if mode is not None:
        os.chmod(full_path, mode)
    return raw_digest(data)


# Schema 2 (PRD-008) adds exact per-operation byte state ("before"/"after":
# exists, sha256, mode), the operation kind and the candidate hash. Schema 1
# evidence still loads; recovery treats its text-only revisions as weaker
# evidence and never guesses when they cannot tell states apart.
# Schema 3 (FILE-INTEGRITY-CONTRACT-001): every revision is a raw-byte digest.
# Schemas 1-2 digested a decoded copy; they equal raw digests for valid UTF-8
# LF files, and recovery fails closed on any other mismatch.
_COMMIT_EVIDENCE_SCHEMA_VERSION = 3

_SUPPORTED_COMMIT_EVIDENCE_SCHEMAS = frozenset({1, 2, 3})

_COMMIT_EVIDENCE_RELATIVE_DIR = os.path.join(".kriya", "control", "commits")


def commit_evidence_dir(workspace_path: str) -> str:
    """D-001: commit evidence is the workspace AUTHORITY's (its repository's), whichever directory of it the commit was made from - an unsettled
    commit made from ``<repo>/sub`` must block ``<repo>`` and the reverse."""
    return os.path.join(authority_root(workspace_path), _COMMIT_EVIDENCE_RELATIVE_DIR)


def candidate_digest(writes: Iterable["StagedFileWrite"], workspace_root: Optional[str]) -> str:
    """Content identity of an exact candidate: path, bytes, mode, deletion.

    The same value is recorded in the RunRecord commit cycle (terminal
    commit seam) and in the commit evidence, so recovery can prove both
    describe the same verified candidate."""
    entries = []
    for item in writes:
        data = item.content_bytes if item.content_bytes is not None else item.content.encode("utf-8")
        relpath = (
            os.path.relpath(item.target_path, workspace_root)
            if workspace_root is not None else item.target_path
        )
        entries.append((
            relpath, "delete" if item.delete else hashlib.sha256(data).hexdigest(), item.mode,
        ))
    return candidate_digest_of_entries(entries)


def candidate_digest_of_entries(
    entries: Iterable[Tuple[str, Optional[str], Optional[int]]],
) -> str:
    """candidate_digest over (relpath, sha256 or "delete", mode) entries, so
    recovery can recompute it from bytes observed on disk."""
    blob = json.dumps(sorted([list(entry) for entry in entries]), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _within_workspace(workspace_path: str, path: str) -> bool:
    root = os.path.realpath(workspace_path)
    candidate = os.path.realpath(path)
    return path_relation(root, candidate) is PathRelation.WITHIN and path_identity(root, candidate) is not PathIdentity.SAME


def _evidence_path(workspace_path: str, transaction_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", transaction_id):
        raise BatchCommitError(f"Invalid commit transaction id: {transaction_id!r}.")
    return os.path.join(commit_evidence_dir(workspace_path), f"{transaction_id}.json")


def _persist_commit_evidence(workspace_path: str, evidence: CommitEvidence) -> str:
    path = _evidence_path(workspace_path, evidence.transaction_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    payload = evidence.to_dict()
    payload["_workspace"] = ownership_metadata(workspace_path)  # D2 M4: every control document names the workspace it was written for
    fd, temp_path = tempfile.mkstemp(prefix=".commit-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(directory)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
    return path


def load_commit_evidence(path: str, *, workspace_path: str) -> CommitEvidence:
    """The evidence document at ``path`` of the workspace ``workspace_path``. D2 M4: its ownership stamp is required and must name this workspace
    (``WorkspaceOwnershipMissing`` / ``WorkspaceOwnershipError``); evidence is what recovery rolls files forward or back from, so a document
    that was not written here is never read."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_ownership(workspace_path, payload, path)
    schema_version = int(payload["schema_version"])
    if schema_version not in _SUPPORTED_COMMIT_EVIDENCE_SCHEMAS:
        raise ValueError(f"Unsupported commit evidence schema: {schema_version}.")
    return CommitEvidence(
        schema_version=schema_version,
        transaction_id=str(payload["transaction_id"]),
        state=CommitState(payload["state"]),
        started_at_unix=float(payload["started_at_unix"]),
        updated_at_unix=float(payload["updated_at_unix"]),
        operations=tuple(payload.get("operations", ())),
        result_revisions=dict(payload.get("result_revisions", {})),
        failure=payload.get("failure"),
        candidate_hash=payload.get("candidate_hash"),
        recovery=payload.get("recovery"),
    )


def list_commit_evidence(
    workspace_path: str,
) -> List[Tuple[str, Optional[CommitEvidence], Optional[str]]]:
    """Every evidence file as (path, evidence, error); unreadable files are
    reported with their error, never skipped."""
    directory = commit_evidence_dir(workspace_path)
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    except FileNotFoundError:
        return []
    entries: List[Tuple[str, Optional[CommitEvidence], Optional[str]]] = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            entries.append((path, load_commit_evidence(path, workspace_path=workspace_path), None))
        except Exception as error:
            entries.append((path, None, f"{type(error).__name__}: {error}"))
    return entries


def settle_recovered_commit_evidence(
    workspace_path: str, expected: CommitEvidence, *, state: CommitState,
    recovery: Dict[str, Any], result_revisions: Dict[str, str],
) -> CommitEvidence:
    """Record the terminal state explicit recovery proved for an interrupted
    transaction (kriya/control/recovery.py). Refuses unless the evidence on
    disk is still exactly ``expected`` and still IN_PROGRESS/UNCERTAIN, so a
    settlement can never overwrite evidence that changed after it was read."""
    if state not in (CommitState.COMMITTED, CommitState.ROLLED_BACK):
        raise BatchCommitError(f"Recovery can only settle to committed/rolled_back, not {state.value}.")
    current = load_commit_evidence(_evidence_path(workspace_path, expected.transaction_id), workspace_path=workspace_path)
    if current != expected or current.state not in (CommitState.IN_PROGRESS, CommitState.UNCERTAIN):
        raise BatchCommitError(
            f"Commit evidence {expected.transaction_id!r} changed since it was assessed; re-run recovery."
        )
    settled = replace(
        current, state=state, updated_at_unix=time.time(),
        result_revisions=dict(result_revisions), recovery=dict(recovery),
    )
    _persist_commit_evidence(workspace_path, settled)
    return settled


def commit_state_for_transaction(workspace_path: str, transaction_id: str) -> CommitState:
    """Return NOT_STARTED when no durable intent exists for this transaction."""
    path = _evidence_path(workspace_path, transaction_id)
    if not os.path.exists(path):
        return CommitState.NOT_STARTED
    return load_commit_evidence(path, workspace_path=workspace_path).state


def find_uncertain_commit_evidence(workspace_path: str) -> Tuple[CommitEvidence, ...]:
    """Return durable commits whose process never recorded a safe terminal state."""
    uncertain = []
    for path, evidence, error in list_commit_evidence(workspace_path):
        if evidence is None:
            raise UncertainCommitError(
                f"Commit evidence {os.path.basename(path)!r} is unreadable; "
                f"source state is uncertain: {error}"
            )
        if evidence.state in (CommitState.IN_PROGRESS, CommitState.UNCERTAIN):
            uncertain.append(evidence)
    return tuple(uncertain)


def _existing_parent(path: str) -> str:
    current = os.path.dirname(path)
    while current and not os.path.isdir(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if not current or not os.path.isdir(current):
        raise BatchCommitError(f"No existing parent directory for target {path!r}.")
    return current


def stage_file_prefix(transaction_id: str) -> str:
    """Name prefix of every staged temp file of one transaction; recorded in
    its commit evidence so a crash's leftovers can be found and removed."""
    return f".kriya-stage-{transaction_id}-"


def _candidate_mode(item: StagedFileWrite) -> int:
    """Permission bits the committed file will have."""
    if item.mode is not None:
        return item.mode & 0o7777
    try:
        return os.stat(item.target_path, follow_symlinks=False).st_mode & 0o7777
    except FileNotFoundError:
        # mkstemp is deliberately restrictive. Match ordinary file-create
        # behavior for a new target while respecting the process umask.
        current_umask = os.umask(0)
        os.umask(current_umask)
        return 0o666 & ~current_umask


def _stage_content(item: StagedFileWrite, transaction_id: str, index: int) -> str:
    parent = _existing_parent(item.target_path)
    fd, path = tempfile.mkstemp(
        prefix=f"{stage_file_prefix(transaction_id)}{index}-", dir=parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                item.content_bytes if item.content_bytes is not None
                else item.content.encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, _candidate_mode(item))
        return path
    except Exception:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise


def _candidate_bytes(item: StagedFileWrite) -> bytes:
    return item.content_bytes if item.content_bytes is not None else item.content.encode("utf-8")


def _file_state(data: Optional[bytes], mode: Optional[int]) -> Dict[str, Any]:
    if data is None:
        return {"exists": False, "sha256": None, "mode": None}
    return {"exists": True, "sha256": hashlib.sha256(data).hexdigest(), "mode": mode}


def _operation_kind(item: StagedFileWrite, target_exists: bool) -> str:
    if item.delete:
        return "DELETE"
    return "MODIFY" if target_exists else "CREATE"


def _preflight_batch(
    staged: List[StagedFileWrite], workspace_path: Optional[str],
) -> None:
    canonical_targets = [fold_name(os.path.realpath(item.target_path)) for item in staged]  # PLAT-018: aliases collide
    if len(set(canonical_targets)) != len(canonical_targets):
        raise BatchCommitError("A candidate batch contains duplicate or aliased target paths.")
    for item in staged:
        if not item.target_path or not item.base_path or not item.expected_base_revision:
            raise BatchCommitError("Every candidate write requires target, base, and expected revision.")
        if workspace_path is not None and not (
            _within_workspace(workspace_path, item.target_path)
            and _within_workspace(workspace_path, item.base_path)
        ):
            raise BatchCommitError(
                f"Candidate target/base escapes workspace {workspace_path!r}: "
                f"{item.target_path!r} (base {item.base_path!r})."
            )
        if os.path.islink(item.target_path):
            # Replacing a link would silently turn it into a regular file and
            # rollback could not restore the link; refuse before mutation.
            raise BatchCommitError(
                f"Candidate file operation cannot target a symbolic link: {item.target_path!r}."
            )
        if os.path.isdir(item.target_path) or os.path.isdir(item.base_path):
            raise BatchCommitError(
                f"Candidate file operation cannot target a directory: {item.target_path!r}."
            )
        base_exists = os.path.exists(item.base_path)
        target_exists = os.path.lexists(item.target_path)
        if item.expected_base_exists is not None and base_exists != item.expected_base_exists:
            raise FileRevisionConflict(
                f"Refusing batch operation for {item.target_path!r}: expected base "
                f"existence={item.expected_base_exists}, found {base_exists}."
            )
        if item.expected_base_exists is False and target_exists:
            raise FileRevisionConflict(
                f"Refusing create operation for existing target {item.target_path!r}."
            )
        if item.delete and item.expected_base_exists is False:
            raise BatchCommitError(
                f"Delete operation for {item.target_path!r} declares a missing expected base."
            )
        if item.delete and not target_exists:
            raise FileRevisionConflict(
                f"Refusing delete operation for missing target {item.target_path!r}."
            )
        actual = read_file_revision(item.base_path)
        if actual != item.expected_base_revision:
            raise FileRevisionConflict(
                f"Refusing stale batch write to '{item.target_path}': base "
                f"'{item.base_path}' expected revision "
                f"{item.expected_base_revision[:12]}, found {actual[:12]}."
            )


def commit_revision_grounded_batch(
    writes: Iterable[StagedFileWrite], workspace_path: Optional[str] = None,
    *, transaction_id: Optional[str] = None,
) -> BatchCommitResult:
    """Apply one local source transaction with durable crash evidence.

    Order of operations (PRD-005):
    1. Whole-batch preflight (containment of target and base, no symlink
       targets, duplicate/alias rejection, create/delete shape, every base
       revision) and refusal while any prior commit is uncertain - all before
       anything is written.
    2. Snapshot every target's bytes and mode.
    3. Persist IN_PROGRESS evidence (fsynced file + directory; each operation's
       kind, exact before/after byte state, base and candidate revisions, and
       the staged-file prefix) BEFORE staging, so no byte - not even a staged
       temp file beside a source file - reaches the workspace before durable
       evidence says a commit began, and anything a crash leaves behind is
       attributable and removable. (The terminal commit seam records RunRecord
       intent before calling this.)
    4. Stage every write as a fully fsynced temp file beside its target.
    5. Apply each operation (os.replace / unlink) after re-checking its base.
    6. Persist COMMITTED evidence. Retention is reference-safe and lives in
       kriya/control/retention.py, never here: evidence is pruned only
       together with the run record that references it.

    Any controlled failure before step 5 changes no source path. A failure
    during step 5 restores every applied path's bytes, existence and mode and
    records ROLLED_BACK. If a rollback step or the terminal evidence write
    itself fails, the evidence is left UNCERTAIN/IN_PROGRESS and
    UncertainCommitError is raised - never an ordinary failure. A batch with
    no writes performs the uncertainty check but writes no evidence.
    """
    staged = list(writes)
    _preflight_batch(staged, workspace_path)
    if workspace_path is not None:
        uncertain = find_uncertain_commit_evidence(workspace_path)
        if uncertain:
            ids = ", ".join(item.transaction_id for item in uncertain)
            raise UncertainCommitError(
                f"Refusing source commit while prior commit intent is uncertain: {ids}."
            )
    if not staged:
        return BatchCommitResult({}, CommitEvidence(
            schema_version=_COMMIT_EVIDENCE_SCHEMA_VERSION,
            transaction_id=transaction_id or "empty", state=CommitState.COMMITTED,
            started_at_unix=time.time(), updated_at_unix=time.time(),
            operations=(), result_revisions={},
        ))

    transaction_id = transaction_id or uuid.uuid4().hex
    if workspace_path is not None:
        _evidence_path(workspace_path, transaction_id)  # validate before any write

    def _rel(path: str) -> str:
        return os.path.relpath(path, workspace_path) if workspace_path is not None else path

    snapshots: Dict[str, Tuple[Optional[bytes], Optional[int]]] = {}
    for item in staged:
        try:
            with open(item.target_path, "rb") as handle:
                snapshots[item.target_path] = (
                    handle.read(), os.stat(item.target_path, follow_symlinks=False).st_mode & 0o7777,
                )
        except FileNotFoundError:
            snapshots[item.target_path] = (None, None)

    started_at = time.time()
    operations = tuple({
        "target_path": _rel(item.target_path),
        "base_path": _rel(item.base_path),
        "operation": "delete" if item.delete else "write",
        "expected_base_revision": item.expected_base_revision,
        "expected_base_exists": item.expected_base_exists,
        "candidate_revision": None if item.delete else raw_digest(_candidate_bytes(item)),
        "stage_prefix": None if item.delete else stage_file_prefix(transaction_id),
        # PRD-008: exact byte state of the target before and after, so crash
        # recovery classifies each path by bytes and mode, not by decoded text.
        "kind": _operation_kind(item, snapshots[item.target_path][0] is not None),
        "before": _file_state(*snapshots[item.target_path]),
        "after": (
            _file_state(None, None) if item.delete
            else _file_state(_candidate_bytes(item), _candidate_mode(item))
        ),
    } for item in staged)
    batch_candidate_hash = (
        candidate_digest(staged, workspace_path) if workspace_path is not None else None
    )

    def _evidence(state: CommitState, **extra: Any) -> CommitEvidence:
        return CommitEvidence(
            schema_version=_COMMIT_EVIDENCE_SCHEMA_VERSION,
            transaction_id=transaction_id, state=state,
            started_at_unix=started_at, updated_at_unix=time.time(),
            operations=operations, candidate_hash=batch_candidate_hash,
            result_revisions=extra.pop("result_revisions", {}), **extra,
        )

    if workspace_path is not None:
        try:
            _persist_commit_evidence(workspace_path, _evidence(CommitState.IN_PROGRESS))
        except Exception as error:
            # _persist_commit_evidence replaces atomically, so no evidence file
            # (i.e. not started) exists and no source path was touched.
            raise BatchCommitError(
                f"Commit intent could not be persisted before mutation: {error}"
            ) from error

    staged_paths: Dict[str, str] = {}
    applied: List[str] = []
    created_directories: List[str] = []
    mutation_started = False
    # Staged files are removed only once durable evidence says the batch is
    # settled (or there is no evidence at all). While it is IN_PROGRESS or
    # UNCERTAIN they may be the only copy of the candidate bytes that
    # `kriya runs recover --complete-partial` needs to finish the commit.
    evidence_settled = workspace_path is None
    try:
        for index, item in enumerate(staged):
            if not item.delete:
                staged_paths[item.target_path] = _stage_content(item, transaction_id, index)
        mutation_started = True
        for item in staged:
            actual = read_file_revision(item.base_path)
            if actual != item.expected_base_revision:
                raise FileRevisionConflict(
                    f"Refusing stale batch write to '{item.target_path}': base "
                    f"changed during commit."
                )
            parent = os.path.dirname(item.target_path)
            missing = []
            cursor = parent
            while cursor and not os.path.exists(cursor):
                missing.append(cursor)
                cursor = os.path.dirname(cursor)
            os.makedirs(parent, exist_ok=True)
            created_directories.extend(reversed(missing))
            _audit_write_file(item.target_path, workspace_path=workspace_path)
            # Track conservatively before the syscall. If an injected
            # wrapper raises after unlink/replace performed its side effect,
            # rollback still restores this target.
            applied.append(item.target_path)
            if item.delete:
                try:
                    os.unlink(item.target_path)
                except FileNotFoundError:
                    pass
            else:
                os.replace(staged_paths[item.target_path], item.target_path)
                staged_paths.pop(item.target_path)
            _fsync_directory(parent)
    except BaseException as commit_error:
        # BaseException: a KeyboardInterrupt/SystemExit mid-apply must still
        # restore the workspace (PRD-005), then propagate unchanged below.
        rollback_errors = []
        for target_path in reversed(applied):
            try:
                original, mode = snapshots[target_path]
                if original is None:
                    try:
                        os.unlink(target_path)
                    except FileNotFoundError:
                        pass
                else:
                    _atomic_write_bytes(target_path, original)
                    if mode is not None:
                        os.chmod(target_path, mode)
                _fsync_directory(os.path.dirname(target_path))
            except Exception as rollback_error:
                rollback_errors.append(f"{target_path}: {rollback_error}")
        for directory in reversed(created_directories):
            try:
                os.rmdir(directory)
            except OSError:
                pass
        failure = f"{type(commit_error).__name__}: {commit_error}"
        terminal_state = CommitState.UNCERTAIN if rollback_errors else CommitState.ROLLED_BACK
        evidence_error: Optional[BaseException] = None
        if workspace_path is not None:
            try:
                _persist_commit_evidence(workspace_path, _evidence(terminal_state, failure=failure))
            except Exception as error:
                evidence_error = error
            else:
                evidence_settled = terminal_state is CommitState.ROLLED_BACK
        if not isinstance(commit_error, Exception):
            # The evidence now says what happened (ROLLED_BACK, or UNCERTAIN/
            # IN_PROGRESS for `kriya runs recover`); the interrupt itself wins.
            raise
        if rollback_errors:
            raise UncertainCommitError(
                f"Candidate commit failed ({commit_error}); rollback also failed for: "
                + "; ".join(rollback_errors)
            ) from commit_error
        if evidence_error is not None:
            # Source paths are restored, but the durable record still says
            # IN_PROGRESS; the state is only provable by explicit recovery.
            raise UncertainCommitError(
                f"Candidate commit failed ({commit_error}) and was rolled back, but the "
                f"rolled-back evidence could not be persisted: {evidence_error}"
            ) from commit_error
        if not mutation_started:
            raise BatchCommitError(f"Candidate staging failed before mutation: {commit_error}") from commit_error
        raise BatchCommitError(
            f"Candidate commit failed and was rolled back: {commit_error}"
        ) from commit_error
    finally:
        if evidence_settled or not mutation_started:
            for path in staged_paths.values():
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    revisions = {
        item.target_path: raw_digest(b"" if item.delete else _candidate_bytes(item))
        for item in staged
    }
    committed_evidence = _evidence(
        CommitState.COMMITTED,
        result_revisions={_rel(path): revision for path, revision in revisions.items()},
    )
    if workspace_path is not None:
        try:
            _persist_commit_evidence(workspace_path, committed_evidence)
        except Exception as error:
            raise UncertainCommitError(
                f"Source changes were applied but committed evidence could not be persisted: {error}"
            ) from error
    return BatchCommitResult(revisions, committed_evidence)

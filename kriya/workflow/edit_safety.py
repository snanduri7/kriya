"""Deterministic sanity checks applied to an anchored edit or full-file write before it reaches disk - whitespace-tolerant anchor matching and structural corruption detection. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization). The "which file does this edit concern" checks that used to live here (find_misdirected_edit_target, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line) moved to kriya/workflow/attribution.py on 2026-08-14 - see that module's own docstring taxonomy for why. What's left here is purely mechanical edit-safety: does the edit apply cleanly, and does the resulting file look structurally sound - never "which file"."""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType

logger = logging.getLogger(__name__)

# MA4.5 (control-plane implementation plan) - audit-only, module-level
# since this file has no class/instance to hold it (unlike kriya/core/llm.py's
# LLMClient or kriya/tools/validate.py's PolymorphicValidator). See
# _audit_write_file below.
_execution_policy = ExecutionPolicy()


def _audit_write_file(full_path: str, workspace_path: Optional[str] = None) -> None:
    """MA4.5 - audit-only ExecutionPolicy consultation, mirroring
    kriya/core/llm.py's _audit_llm_network_access (MA4.3) and kriya/tools/
    validate.py's _audit_run_command (MA4.4) exactly: can never affect
    whether atomic_write_file actually writes - any exception raised here is
    caught and logged, never propagated, and the decision is only logged,
    never branched on.

    AuthorizedFileWriter callers supply their already-validated workspace
    root so this second audit reports the same containment fact accurately.
    Low-level compatibility callers may omit it; those retain the historical
    context-free default-deny audit without changing write behavior.

    MA4.16 update: this is no longer the only policy consultation Kriya's
    two real content-write call sites (kriya/workflow/attempt.py,
    kriya/workflow/self_correction.py) go through. Both now call
    kriya/policy/filesystem.py's AuthorizedFileWriter FIRST, with the real
    worktree_path they've always had in scope - that layer REALLY enforces
    containment and a narrow sensitive-path check (raises PolicyDeniedError,
    nothing reaches this function at all on a denial) before
    commit_revision_grounded_file/batch are ever called. This audit-only
    call therefore now only fires for writes that already passed real
    enforcement upstream, plus any other/future caller of
    atomic_write_file directly - it's a second, defense-in-depth signal,
    not the only one anymore."""
    try:
        result = _execution_policy.evaluate(
            ActionRequest(
                action_type=ActionType.WRITE_FILE, target=full_path,
                workspace_path=workspace_path,
            )
        )
        logger.debug(
            "MA4 policy audit (not enforced): WRITE_FILE '%s' -> %s (%s)",
            full_path, result.decision.value, result.reason_code,
        )
    except Exception as e:
        logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)


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


def content_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_file_revision(full_path: str) -> str:
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            return content_revision(fh.read())
    except FileNotFoundError:
        return content_revision("")


def commit_revision_grounded_file(
    full_path: str, content: str, expected_revision: str,
    workspace_path: Optional[str] = None,
) -> str:
    """Atomically commit a fully staged file only if its base is unchanged."""
    actual_revision = read_file_revision(full_path)
    if actual_revision != expected_revision:
        raise FileRevisionConflict(
            f"Refusing stale write to '{full_path}': expected revision "
            f"{expected_revision[:12]}, found {actual_revision[:12]}. Re-read the file and retry."
        )
    atomic_write_file(full_path, content, workspace_path=workspace_path)
    return content_revision(content)


def atomic_write_file(
    full_path: str, content: str, workspace_path: Optional[str] = None,
) -> None:
    """Writes `content` to `full_path` atomically - via a temp file in the SAME
    directory, then os.replace() (atomic on both POSIX and Windows NTFS) - so a
    process killed mid-write can never leave `full_path` truncated/corrupted at
    0 bytes with whatever content was previously there already destroyed.

    Confirmed live, 2026-08-16: a real eval-harness run's `--timeout-per-goal`
    fired mid-write on a full-set fallback-model regeneration - `subprocess.run`'s
    own timeout handling calls Popen.kill() (SIGKILL on POSIX), which is
    uncatchable, so no signal handler or cleanup code could ever run regardless
    of how this were structured. The one thing that CAN survive an uncatchable
    kill is making each individual write itself atomic: plain `open(path, "w")`
    truncates the file to empty BEFORE any new content is written, so a kill in
    that window leaves 0 bytes on disk - exactly what was found post-mortem,
    destroying the one piece of evidence (the file's real content at the moment
    of a still-unexplained recurring compile failure) that would have settled
    root cause. With this, the file on disk is always EITHER the complete old
    content OR the complete new content, never an in-between state, regardless
    of when the kill lands.

    Used by both kriya/workflow/attempt.py (the normal per-attempt write path)
    and kriya/workflow/self_correction.py (the micro-loop's own tool-driven
    patch application) - lives here, not in either caller, since both already
    import from this module and neither should import from the other (attempt.py
    only imports self_correction.py locally, deferred inside a function, to
    avoid exactly that).

    The temp file lives in the same directory as the target (not a shared
    system tmp dir) so os.replace() stays within one filesystem - crossing
    filesystems silently degrades to a non-atomic copy+delete on some
    platforms, defeating the whole point."""
    _audit_write_file(full_path, workspace_path=workspace_path)
    tmp_path = f"{full_path}.kriya-tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, full_path)


def _atomic_write_bytes(full_path: str, content: bytes) -> None:
    tmp_path = f"{full_path}.kriya-rollback-{os.getpid()}"
    with open(tmp_path, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, full_path)


_COMMIT_EVIDENCE_SCHEMA_VERSION = 1
_COMMIT_EVIDENCE_RELATIVE_DIR = os.path.join(".kriya", "control", "commits")


def _within_workspace(workspace_path: str, path: str) -> bool:
    root = os.path.realpath(workspace_path)
    candidate = os.path.realpath(path)
    try:
        return os.path.commonpath((root, candidate)) == root and candidate != root
    except ValueError:
        return False


def _fsync_directory(path: str) -> None:
    """Best-effort metadata durability on platforms supporting directory fsync."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _evidence_path(workspace_path: str, transaction_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", transaction_id):
        raise BatchCommitError(f"Invalid commit transaction id: {transaction_id!r}.")
    return os.path.join(
        workspace_path, _COMMIT_EVIDENCE_RELATIVE_DIR, f"{transaction_id}.json",
    )


def _persist_commit_evidence(workspace_path: str, evidence: CommitEvidence) -> str:
    path = _evidence_path(workspace_path, evidence.transaction_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    payload = evidence.to_dict()
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


def load_commit_evidence(path: str) -> CommitEvidence:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    schema_version = int(payload["schema_version"])
    if schema_version != _COMMIT_EVIDENCE_SCHEMA_VERSION:
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
    )


def commit_state_for_transaction(workspace_path: str, transaction_id: str) -> CommitState:
    """Return NOT_STARTED when no durable intent exists for this transaction."""
    path = _evidence_path(workspace_path, transaction_id)
    if not os.path.exists(path):
        return CommitState.NOT_STARTED
    return load_commit_evidence(path).state


def find_uncertain_commit_evidence(workspace_path: str) -> Tuple[CommitEvidence, ...]:
    """Return durable commits whose process never recorded a safe terminal state."""
    directory = os.path.join(workspace_path, _COMMIT_EVIDENCE_RELATIVE_DIR)
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    except FileNotFoundError:
        return ()
    uncertain = []
    for name in names:
        try:
            evidence = load_commit_evidence(os.path.join(directory, name))
        except Exception as error:
            raise UncertainCommitError(
                f"Commit evidence {name!r} is unreadable; source state is uncertain: {error}"
            ) from error
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


def _stage_content(item: StagedFileWrite, transaction_id: str, index: int) -> str:
    parent = _existing_parent(item.target_path)
    fd, path = tempfile.mkstemp(
        prefix=f".kriya-stage-{transaction_id}-{index}-", dir=parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(item.content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            mode = os.stat(item.target_path, follow_symlinks=False).st_mode & 0o7777
        except FileNotFoundError:
            # mkstemp is deliberately restrictive. Match ordinary file-create
            # behavior for a new target while respecting the process umask.
            current_umask = os.umask(0)
            os.umask(current_umask)
            mode = 0o666 & ~current_umask
        os.chmod(path, mode)
        return path
    except Exception:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise


def _preflight_batch(
    staged: List[StagedFileWrite], workspace_path: Optional[str],
) -> None:
    canonical_targets = [os.path.normcase(os.path.realpath(item.target_path)) for item in staged]
    if len(set(canonical_targets)) != len(canonical_targets):
        raise BatchCommitError("A candidate batch contains duplicate or aliased target paths.")
    for item in staged:
        if not item.target_path or not item.base_path or not item.expected_base_revision:
            raise BatchCommitError("Every candidate write requires target, base, and expected revision.")
        if workspace_path is not None and not _within_workspace(
            workspace_path, item.target_path,
        ):
            raise BatchCommitError(
                f"Candidate target escapes workspace {workspace_path!r}: "
                f"{item.target_path!r}."
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

    Guarantees already present before PRD-005 were whole-batch revision
    preflight, per-file atomic replacement, duplicate rejection, and rollback
    of earlier successful writes after a later controlled failure. PRD-005
    closes the remaining boundaries: canonical containment/operation preflight,
    staging and snapshotting before mutation, mode preservation, rollback even
    when a fault fires immediately after a filesystem operation, directory
    durability, and a durable in-progress/committed/rolled-back record. Absence
    of a record means not started; an in-progress record left by process death
    is explicitly uncertain and blocks another commit.
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

    transaction_id = transaction_id or uuid.uuid4().hex
    started_at = time.time()
    operations = tuple({
        "target_path": (
            os.path.relpath(item.target_path, workspace_path)
            if workspace_path is not None else item.target_path
        ),
        "base_path": (
            os.path.relpath(item.base_path, workspace_path)
            if workspace_path is not None else item.base_path
        ),
        "operation": "delete" if item.delete else "write",
        "expected_base_revision": item.expected_base_revision,
        "expected_base_exists": item.expected_base_exists,
    } for item in staged)
    snapshots: Dict[str, Tuple[Optional[bytes], Optional[int]]] = {}
    for item in staged:
        try:
            with open(item.target_path, "rb") as handle:
                snapshots[item.target_path] = (
                    handle.read(), os.stat(item.target_path, follow_symlinks=False).st_mode & 0o7777,
                )
        except FileNotFoundError:
            snapshots[item.target_path] = (None, None)

    staged_paths: Dict[str, str] = {}
    try:
        for index, item in enumerate(staged):
            if not item.delete:
                staged_paths[item.target_path] = _stage_content(item, transaction_id, index)
    except Exception as error:
        for path in staged_paths.values():
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        raise BatchCommitError(f"Candidate staging failed before mutation: {error}") from error

    evidence = CommitEvidence(
        schema_version=_COMMIT_EVIDENCE_SCHEMA_VERSION,
        transaction_id=transaction_id,
        state=CommitState.IN_PROGRESS,
        started_at_unix=started_at,
        updated_at_unix=time.time(),
        operations=operations,
        result_revisions={},
    )
    if workspace_path is not None:
        _persist_commit_evidence(workspace_path, evidence)

    applied: List[str] = []
    created_directories: List[str] = []
    try:
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
            if item.delete:
                _audit_write_file(item.target_path, workspace_path=workspace_path)
                # Track conservatively before the syscall. If an injected
                # wrapper raises after unlink/replace performed its side
                # effect, rollback still restores this target.
                applied.append(item.target_path)
                try:
                    os.unlink(item.target_path)
                except FileNotFoundError:
                    pass
            else:
                _audit_write_file(item.target_path, workspace_path=workspace_path)
                applied.append(item.target_path)
                staged_path = staged_paths[item.target_path]
                os.replace(staged_path, item.target_path)
                staged_paths.pop(item.target_path)
            _fsync_directory(parent)
    except Exception as commit_error:
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
            except Exception as rollback_error:  # pragma: no cover - rare OS failure
                rollback_errors.append(f"{target_path}: {rollback_error}")
        for directory in reversed(created_directories):
            try:
                os.rmdir(directory)
            except OSError:
                pass
        terminal_state = CommitState.UNCERTAIN if rollback_errors else CommitState.ROLLED_BACK
        terminal_evidence = CommitEvidence(
            schema_version=_COMMIT_EVIDENCE_SCHEMA_VERSION,
            transaction_id=transaction_id,
            state=terminal_state,
            started_at_unix=started_at,
            updated_at_unix=time.time(),
            operations=operations,
            result_revisions={},
            failure=f"{type(commit_error).__name__}: {commit_error}",
        )
        if workspace_path is not None:
            try:
                _persist_commit_evidence(workspace_path, terminal_evidence)
            except Exception as evidence_error:
                rollback_errors.append(f"commit evidence: {evidence_error}")
        if rollback_errors:
            raise BatchCommitError(
                f"Candidate commit failed ({commit_error}); rollback also failed for: "
                + "; ".join(rollback_errors)
            ) from commit_error
        raise BatchCommitError(
            f"Candidate commit failed and was rolled back: {commit_error}"
        ) from commit_error
    finally:
        for path in staged_paths.values():
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    revisions = {
        item.target_path: content_revision("" if item.delete else item.content)
        for item in staged
    }
    evidence_revisions = {
        (os.path.relpath(path, workspace_path) if workspace_path is not None else path): revision
        for path, revision in revisions.items()
    }
    committed_evidence = CommitEvidence(
        schema_version=_COMMIT_EVIDENCE_SCHEMA_VERSION,
        transaction_id=transaction_id,
        state=CommitState.COMMITTED,
        started_at_unix=started_at,
        updated_at_unix=time.time(),
        operations=operations,
        result_revisions=evidence_revisions,
    )
    if workspace_path is not None:
        try:
            _persist_commit_evidence(workspace_path, committed_evidence)
        except Exception as error:
            raise UncertainCommitError(
                f"Source changes were applied but committed evidence could not be persisted: {error}"
            ) from error
    return BatchCommitResult(revisions, committed_evidence)


def normalize_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def apply_anchored_edits(original_content: str, edits: List[Dict[str, str]], shown_context: str) -> str:
    current_content = original_content
    for idx, edit in enumerate(edits, 1):
        search_block = edit.get("search", "")
        replace_block = edit.get("replace", "")

        if not search_block:
            continue

        norm_search = normalize_whitespace(search_block)

        # Found live, 2026-08-17, digging into a corpus-wide survey of
        # eval-harness runs: 14 "elided in the skeletonized context"
        # failures across the whole run history, several from a genuinely
        # legitimate shape this check never accounted for. shown_context is
        # a fixed snapshot, passed in once and never updated across loop
        # iterations - but current_content DOES evolve as earlier edits in
        # this SAME response get applied (the .replace() call below).
        # Reproduced directly: a two-step chained edit (edit #1 adds a
        # `helper();` call, edit #2 wants to comment on that exact new
        # line) is completely valid and internally consistent, but edit
        # #2's search text was never part of the ORIGINAL file the model
        # was shown - only of what edit #1 itself just introduced - so the
        # old check (comparing only against the static shown_context)
        # wrongly rejected it as "not shown to the model," when the model
        # in fact introduced that exact text itself, one edit earlier in
        # the same response. Grounding a search block against EITHER the
        # original shown context OR the file's current (possibly
        # already-edited) state closes this gap while still rejecting a
        # genuinely fabricated/hallucinated search block, which by
        # definition matches neither.
        if shown_context:
            norm_shown = normalize_whitespace(shown_context)
            norm_current = normalize_whitespace(current_content)
            if norm_search not in norm_shown and norm_search not in norm_current:
                raise ValueError(
                    f"Anchor matching failed for edit #{idx}: The search block contains code segments "
                    f"that were elided in the skeletonized context and not shown to the model."
                )

        exact_count = current_content.count(search_block)
        if exact_count >= 1:
            # An exact (unnormalized) match exists - the strictest, most-
            # preferred match shape, so its own count is the uniqueness
            # signal here, not a whitespace-normalized count over the whole
            # file (see the window branch below for why that can disagree
            # with what's actually being matched).
            if exact_count > 1:
                raise ValueError(
                    f"Anchor matching failed for edit #{idx}: The search block matched {exact_count} times (must match exactly once). "
                    f"Provide more context surrounding the search block."
                )
            current_content = current_content.replace(search_block, replace_block, 1)
            continue

        # No exact match - fall back to a whitespace-tolerant search that
        # also tolerates a DIFFERENT number of blank lines between content
        # and search block, not just different indentation - consistent with
        # normalize_whitespace's own blank-line-discarding philosophy used
        # everywhere else in this function (the shown_context check above,
        # for instance). A prior version used a FIXED-size raw-line window
        # (exactly len(search_block.splitlines()) raw lines) for both the
        # uniqueness check and the actual splice - found live, 2026-08-11
        # (kriya-oneshot-protocol-ignite-qpid audit): a search block with one
        # blank line between two statements, matched against content with
        # TWO blank lines at the same location, made the OLD whole-file
        # blank-line-collapsed uniqueness check report "exactly 1 match" (it
        # discards all blank lines before counting) while the fixed-size
        # window could never actually find it (the real match needs one more
        # raw line than the search block has) - the check said "found,
        # unique" while application then failed with "could not find match",
        # a self-contradictory outcome that burned a retry on Kriya's own
        # matching inconsistency, not a real problem with the edit.
        #
        # Matches the search block's own non-blank, stripped lines as a
        # contiguous subsequence against the content's non-blank, stripped
        # lines - the count of subsequence matches IS the uniqueness check
        # (no separate, disagreeing mechanism), and the actual RAW splice
        # range spans from the first to the last matched non-blank line's
        # real index, so any blank lines interspersed between them in the
        # original file are naturally included in (and replaced by) the
        # spliced-in replace_block, regardless of how many there are.
        search_norm_lines = [ln.strip() for ln in search_block.splitlines() if ln.strip()]
        content_lines = current_content.splitlines()
        content_nonblank = [(i, ln.strip()) for i, ln in enumerate(content_lines) if ln.strip()]
        content_norm_lines = [ln for _, ln in content_nonblank]

        n = len(search_norm_lines)
        matched_starts = (
            [i for i in range(len(content_norm_lines) - n + 1) if content_norm_lines[i:i + n] == search_norm_lines]
            if n > 0 else []
        )

        if not matched_starts:
            raise ValueError(
                f"Anchor matching failed for edit #{idx}: The search block matched 0 times. "
                f"Please ensure whitespace and contents match exactly."
            )
        elif len(matched_starts) > 1:
            raise ValueError(
                f"Anchor matching failed for edit #{idx}: The search block matched {len(matched_starts)} times (must match exactly once). "
                f"Provide more context surrounding the search block."
            )

        match_pos = matched_starts[0]
        raw_start = content_nonblank[match_pos][0]
        raw_end = content_nonblank[match_pos + n - 1][0] + 1
        content_lines[raw_start:raw_end] = replace_block.splitlines()
        current_content = "\n".join(content_lines)

    return current_content


def _strip_java_comments_and_strings(code: str) -> str:
    """Best-effort removal of Java string/char literals and // and /* */
    comments, replacing each with equal-length whitespace (blank, not deleted,
    so a caller relying on absolute character positions for anything else
    isn't affected). Deliberately NOT a real lexer - doesn't understand Java
    17 text blocks (\"\"\"...\"\"\") or unicode escapes. Good enough for a
    cheap, best-effort structural pre-check (find_structural_corruption
    below); a false negative here just means that check degrades to "found
    nothing wrong" the same as if the check didn't exist, never a false
    rejection of valid code."""
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in code[i:j]))
            i = j
        elif c in ("\"", "'"):
            quote = c
            j = i + 1
            while j < n and code[j] != quote:
                j += 2 if code[j] == "\\" else 1
            j = min(j + 1, n)
            out.append("".join(ch if ch == "\n" else " " for ch in code[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


_TOP_LEVEL_TYPE_RE = re.compile(r'\b(?:class|interface|enum)\s+(\w+)')


def _find_duplicate_top_level_type(stripped: str) -> Optional[str]:
    """Scans (already comment/string-stripped) Java source for two top-level
    class/interface/enum declarations sharing the same name - the variant a
    plain brace-balance count can never catch, since a *complete*, self-
    closed duplicate type is brace-balanced by construction (2026-08-12 SME
    review; see find_structural_corruption's own docstring for the real
    incident this closes). "Top-level" is determined by brace depth at the
    keyword's position (0 = not nested inside another type's body) so a
    legitimate, differently-named inner/nested class is never flagged -
    only a second declaration of a name already seen at depth 0 is."""
    seen = set()
    for m in _TOP_LEVEL_TYPE_RE.finditer(stripped):
        depth = stripped.count("{", 0, m.start()) - stripped.count("}", 0, m.start())
        if depth != 0:
            continue
        name = m.group(1)
        if name in seen:
            return name
        seen.add(name)
    return None


def find_structural_corruption(filepath: str, content: str) -> Optional[str]:
    """Cheap, deterministic, best-effort structural sanity check on a file
    about to be written to the sandbox - catches the exact corruption class
    found live TWICE this session: a duplicated package/class declaration
    from a swallowed FILE CONTENT: block (2026-08-04, see
    _split_fix_analysis_edit's own docstring) and a 23-error "illegal start
    of expression"/"class, interface, enum, or record expected" cascade from
    a fallback model's full-file rewrite (2026-08-08, ignite_qpid_protocol) -
    BEFORE the expensive compile gate spends a full Maven invocation
    discovering the same thing. Neither prior incident was caught by Layer 1
    (find_edits_ignoring_reported_line) or any scaffold, since both checks
    something about a REPORTED error's location/shape - a corrupted file from
    a clean-looking edit or a fresh full-file generation has no prior error
    to check against at all.

    Deliberately NOT a full AST/tree-sitter integration for every language -
    that would catch more but at real added complexity and (for Java/Ruby) a
    new dependency Kriya has repeatedly avoided elsewhere (e.g. the hand-
    rolled LSP client). This targets specifically the cheap, unambiguous
    "obviously broken" shape a human would spot on sight (unbalanced braces,
    a duplicated declaration, malformed XML), the same shape every real
    corruption incident actually was. Returns a human-readable description
    of the problem, or None if the file looks structurally sound - never a
    guarantee of correctness (a real compiler/interpreter remains the source
    of truth for that), only a cheap, low-false-positive earlier tripwire.
    (find_edits_ignoring_reported_line now lives in
    kriya/workflow/attribution.py, moved there 2026-08-14 alongside the
    rest of the "which file" checks - this reference is to the check
    itself, not its current file location.)

    Scoped to .java (brace balance, comment/string-aware so a stray brace
    inside a string literal or comment doesn't produce a false positive,
    PLUS a duplicate-top-level-type check for the one documented gap a brace
    count alone can't catch - see _find_duplicate_top_level_type) and .xml
    (real well-formedness via the stdlib's own XML parser, not a heuristic -
    zero false positives by construction). Every other extension returns
    None unconditionally.

    Deliberately NOT extended to .py, despite a real stdlib ast.parse()
    check being zero-cost and zero-new-dependency (tried during the
    2026-08-12 SME review, reverted after live test-writing surfaced why):
    unlike Java (where this function's brace check only intercepts ONE
    narrow shape, leaving most javac syntax errors to reach the real compile
    gate - which DOES show the model its previous broken content in the
    retry prompt, since that gate only runs on already-written files),
    Python's own "compile check" (kriya/tools/validate.py) is JUST
    `compile(source, f, "exec")` - a pure syntax check with 100% overlap
    with what ast.parse() would catch. Adding it here would silently
    redirect EVERY Python syntax error away from the compile gate's richer,
    content-shown targeted retry (this function runs pre-write, so a
    rejected file is deliberately never added to all_files_written, and
    _build_targeted_retry_prompt only shows previous content for files it
    finds on disk) into this function's leaner error-text-only failure -
    for zero cost savings, since compile() is already just as free as
    ast.parse(), with no expensive gate being avoided the way a real Maven
    invocation is for Java. Not extended to Ruby either, for the original
    reason: indentation/block-keyword-based, not brace-delimited, and no
    stdlib parser available - a brace count is much less informative there."""
    if filepath.endswith(".java"):
        stripped = _strip_java_comments_and_strings(content)
        balance = stripped.count("{") - stripped.count("}")
        if balance > 0:
            return f"{balance} unclosed '{{' brace(s) - more opening braces than closing ones."
        if balance < 0:
            return f"{-balance} extra closing '}}' brace(s) - more closing braces than opening ones."
        duplicate = _find_duplicate_top_level_type(stripped)
        if duplicate:
            return f"duplicate top-level type declaration: '{duplicate}' is declared more than once."
        # A complete compilation unit followed by model commentary is often
        # brace-balanced and therefore invisible to the checks above.  At
        # least one top-level type means the last top-level closing brace is
        # the end of Java source; non-whitespace afterward is payload
        # contamination, not valid source. Comments/strings have already been
        # blanked by the language adapter, avoiding marker-word heuristics.
        if _TOP_LEVEL_TYPE_RE.search(stripped):
            last_close = stripped.rfind("}")
            trailing = stripped[last_close + 1:]
            # Java permits empty top-level declarations (`;`) between/after
            # type declarations; they are source, not model commentary.
            if last_close >= 0 and trailing.replace(";", "").strip():
                return "non-source payload appears after the final top-level type declaration."
    elif filepath.endswith(".xml"):
        try:
            ET.fromstring(content)
        except ET.ParseError as ex:
            return f"malformed XML: {ex}"
    return None


def find_cross_file_type_conflict(
    filepath: str,
    candidate_type_names: List[str],
    type_index: Dict[str, List[str]],
) -> Optional[Tuple[str, List[str]]]:
    """The cross-file sibling of _find_duplicate_top_level_type above - that
    one catches two declarations of the same type WITHIN one file; this one
    catches a new file about to be written whose declared type already
    exists somewhere ELSE in the workspace, found live 2026-08-21
    (protocol_encoder_java): three separate, incompatible `Protocol.java`
    files ended up coexisting in different packages, each missing different
    pieces of the intended API, because nothing noticed a "new" file was
    actually redeclaring an existing type under a different path.

    Deliberately pure data in/out (no DependencyGraph/DB coupling) so it's
    trivially unit-testable with hand-built inputs, matching
    find_whole_response_no_op(edits)'s own style - the caller
    (kriya/workflow/attempt.py) is responsible for building `type_index`
    (kriya/analyzer/graph.py::DependencyGraph.get_class_symbol_locations(),
    layered with anything written earlier in the same still-in-progress
    attempt) and extracting `candidate_type_names`
    (DependencyGraph.extract_class_names()) before calling this.

    Scoped to genuinely NEW files by the caller, never a REPAIR of a file
    that already legitimately owns `filepath` - `filepath` itself is
    excluded from the conflict set here defensively (a file redeclaring its
    OWN class is never a conflict), but the caller should not even reach
    this for an existing-path write in the first place.

    Returns (type_name, [other_paths]) for the FIRST candidate name also
    declared elsewhere, or None if none conflict."""
    for name in candidate_type_names:
        others = [p for p in type_index.get(name, []) if p != filepath]
        if others:
            return name, others
    return None

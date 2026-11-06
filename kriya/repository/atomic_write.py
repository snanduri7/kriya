"""REPOSITORY: the atomic replacement of one file (full modularization, slice M1; MODULE-CONTRACTS §11).

Moved verbatim from kriya/workflow/edit_safety.py, which re-exports ``atomic_write_file`` (and the audit policy
instance its tests patch) until P1: the temp-file + ``os.replace`` write of one file (``atomic_write_file``), the
fsynced rollback write (``_atomic_write_bytes``), the best-effort directory fsync (``_fsync_directory``) and the MA4.5
audit-only ExecutionPolicy consultation every write makes (``_audit_write_file``). Kriya-owned files only: a candidate
write reaches this through kriya/policy/filesystem.py's AuthorizedFileWriter and kriya/repository/commit.py.
"""

import contextlib
import logging
import os
import uuid
from typing import Optional

from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType

# The pre-move logger name: run logs carry %(name)s, so the audit lines read exactly as before the move.
logger = logging.getLogger("kriya.workflow.edit_safety")


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


def atomic_write_file(
    full_path: str, content: str, workspace_path: Optional[str] = None,
    *, content_bytes: Optional[bytes] = None, durable: bool = False, mode: Optional[int] = None,
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

    WAVE8 OI-03: the write is also DURABLE (see durable_atomic_write_bytes): a power loss or crash after this returns cannot leave the
    destination renamed-but-empty. The contract is the OS/filesystem one (fsync + rename + directory fsync); on macOS ``os.fsync`` is not
    ``F_FULLFSYNC`` and no claim is made about the drive's own write cache.

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
    data = content_bytes if content_bytes is not None else content.encode("utf-8")
    # WAVE8 OI-03 / OI-04 (owner 2026-11-06): EVERY atomic write is crash-durable and the one place that makes it so is
    # ``durable_atomic_write_bytes``: temp file in the destination directory -> write -> flush -> fsync(temp) -> atomic replace ->
    # fsync(parent directory) on POSIX; a failure at any step raises ``DurableWriteError`` (an ``OSError``), removes the temp file
    # and leaves the destination intact (before the replace) or visible-but-not-known-durable (after it). ``durable`` is kept as an
    # accepted argument for source compatibility only: a non-durable write no longer exists, so callers carry no durability code.
    del durable
    durable_atomic_write_bytes(full_path, data, mode=mode)


DURABLE_WRITE_STEPS = ("temp_write", "file_fsync", "replace", "directory_fsync")


class DurableWriteError(OSError):
    """D1: an authoritative durable transition did not complete. ``step`` is one of DURABLE_WRITE_STEPS and names the first operation that failed; the
    original error is ``__cause__``. After ``replace`` the new content may already be visible but is NOT known to be durable, so the caller must treat the
    transition as failed either way (a STARTED transition: the effect must not run; a COMPLETED transition: no authoritative success)."""

    def __init__(self, step: str, path: str, cause: BaseException) -> None:
        super().__init__(f"durable write of {path!r} failed at {step}: {cause}")
        self.step = step
        self.path = path


def durable_atomic_write_bytes(full_path: str, data: bytes, *, mode: Optional[int] = None) -> None:
    """The portable crash/restart durability contract for an authoritative transition: temp write -> file fsync -> atomic replace -> parent-directory fsync.

    EVERY operation is required: a failure in any of them raises DurableWriteError (a temp file left behind before the replace is removed). This is the OS /
    filesystem contract of fsync + rename + directory fsync; it makes no claim about physical-media or power-loss persistence beyond it (no F_FULLFSYNC).
    WAVE8 OI-03: this is the ONLY write path of ``atomic_write_file`` (no caller opts out). Windows has no directory fsync (``os.open`` of a directory is refused);
    there the directory step is skipped and the contract is the file fsync + the atomic ``os.replace`` (NTFS journals the rename), stated, not claimed."""
    directory = os.path.dirname(os.path.abspath(full_path))
    tmp_path = f"{full_path}.kriya-durable-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    step = "temp_write"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            step = "file_fsync"
            os.fsync(handle.fileno())
        step = "replace"
        os.replace(tmp_path, full_path)
    except Exception as error:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise DurableWriteError(step, full_path, error) from error
    try:
        _fsync_directory(directory, strict=True)
    except OSError as error:
        raise DurableWriteError("directory_fsync", full_path, error) from error


def confirm_visible_state_durable(full_path: str) -> None:
    """The READ side of the durable-transition contract (DURABLE-DISPOSITION-DIRECTORY-FSYNC-VISIBLE-BEFORE-DURABLE-001): establish that the bytes now visible at ``full_path``
    and their parent-directory entry are durable, so a reader may treat them as authoritative.

    A writer whose transition failed at ``directory_fsync`` leaves the new bytes VISIBLE but not known to be durable; visibility is not durability. A reader about to let such a state
    CLEAR a gate calls this first: success means the visible state is the durable one (nothing is rewritten, so it is idempotent); failure raises DurableWriteError (step
    ``file_fsync`` or ``directory_fsync``) and the reader must stay blocking. Same OS contract as durable_atomic_write_bytes (fsync + directory fsync, no F_FULLFSYNC), and strict:
    it may never swallow an error."""
    step = "file_fsync"
    try:
        descriptor = os.open(full_path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        step = "directory_fsync"
        _fsync_directory(os.path.dirname(os.path.abspath(full_path)), strict=True)
    except OSError as error:
        raise DurableWriteError(step, full_path, error) from error


def _atomic_write_bytes(full_path: str, content: bytes) -> None:
    tmp_path = f"{full_path}.kriya-rollback-{os.getpid()}"
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, full_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    _fsync_directory(os.path.dirname(os.path.abspath(full_path)))  # WAVE8 OI-03: the restored entry is durable too (best effort: a rollback never masks its cause)


def _fsync_directory(path: str, *, strict: bool = False) -> None:
    """Metadata durability of a directory entry. Best-effort by default (platforms that cannot fsync a directory are tolerated); ``strict=True`` is for an
    authoritative durable transition (D1) and lets every error propagate - it may never swallow one."""
    if os.name == "nt":  # no directory fsync on Windows (a directory cannot be opened for it); see durable_atomic_write_bytes
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        if strict:
            raise
        return
    try:
        os.fsync(descriptor)
    except OSError:
        if strict:
            raise
    finally:
        os.close(descriptor)

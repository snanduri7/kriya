"""The binding between a verified candidate and the batch that is committed
(CANDIDATE-VERIFIED-DIGEST-BINDING-001).

Terminal verification judges a candidate; the terminal commit writes a batch
it materializes again later. Nothing else ties the two together: with static
analysis disabled, a candidate changed between its last deterministic gate and
the commit would be committed as if it were the verified one. So each commit
path binds the exact batch when verification starts
(:func:`bind_candidate`), and ``terminal_commit.commit_terminal_candidate``
recomputes the binding from the batch it is about to write, before the first
real-workspace byte, and refuses anything that differs.

The binding is Kriya's own record of the batch, independent of every
provider and of static analysis: one entry per file (normalized
workspace-relative path, operation, exact bytes digest or deletion marker,
mode, base revision and base existence), serialized canonically and digested.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Tuple

BINDING_VERSION = 1

VERIFIED_CANDIDATE_EVIDENCE_STALE = "VERIFIED_CANDIDATE_EVIDENCE_STALE"
VERIFIED_CANDIDATE_EVIDENCE_MISSING = "VERIFIED_CANDIDATE_EVIDENCE_MISSING"

# (relpath, operation, sha256 of the bytes or None for a deletion, mode,
# expected base revision, expected base existence)
BindingEntry = Tuple[str, str, Optional[str], Optional[int], str, Optional[bool]]


class VerifiedCandidateStale(RuntimeError):
    """The batch about to be committed is not the batch that was verified."""


@dataclass(frozen=True)
class CandidateVerificationBinding:
    digest: str
    entries: Tuple[BindingEntry, ...]


def _operation(write: Any) -> str:
    if write.delete:
        return "delete"
    if write.expected_base_exists is None:
        # The batch declares only the base revision (the direct path cannot
        # tell an empty existing file from a missing one); that revision is
        # bound either way.
        return "write"
    return "modify" if write.expected_base_exists else "add"


def _relpath(target_path: str, workspace_root: str) -> str:
    """Workspace-relative with posix separators, comparing real paths, so a
    workspace reached through a symlink gives the same path."""
    real_target = os.path.join(
        os.path.realpath(os.path.dirname(os.path.abspath(target_path))), os.path.basename(target_path),
    )
    return os.path.relpath(real_target, os.path.realpath(workspace_root)).replace(os.sep, "/")


def bind_candidate(writes: Iterable[Any], workspace_path: str) -> CandidateVerificationBinding:
    """The binding of an exact commit batch (``StagedFileWrite``s)."""
    entries = []
    for write in writes:
        data = write.content_bytes if write.content_bytes is not None else write.content.encode("utf-8")
        entries.append((
            _relpath(write.target_path, workspace_path), _operation(write),
            None if write.delete else hashlib.sha256(data).hexdigest(), write.mode,
            write.expected_base_revision, write.expected_base_exists,
        ))
    ordered = tuple(sorted(entries, key=lambda entry: (entry[0], entry[1])))
    blob = json.dumps({"version": BINDING_VERSION, "entries": ordered}, sort_keys=True, separators=(",", ":"))
    return CandidateVerificationBinding(
        digest=hashlib.sha256(blob.encode("utf-8")).hexdigest(), entries=ordered,
    )


def binding_refusal(
    verified: Optional[CandidateVerificationBinding], writes: Iterable[Any], workspace_path: str,
) -> Optional[Tuple[str, str]]:
    """(reason code, detail) when ``writes`` may not be committed as the
    verified candidate, None when it is exactly the verified batch."""
    if verified is None:
        return VERIFIED_CANDIDATE_EVIDENCE_MISSING, (
            "no verification binding exists for this candidate; refusing to commit unverified bytes")
    current = bind_candidate(writes, workspace_path)
    if current.digest == verified.digest:
        return None
    return VERIFIED_CANDIDATE_EVIDENCE_STALE, (
        "the candidate changed after terminal verification; changed paths: "
        + ", ".join(_changed_paths(verified.entries, current.entries))
    )


def _changed_paths(verified: Tuple[BindingEntry, ...], current: Tuple[BindingEntry, ...]) -> list:
    before = {entry[0]: entry for entry in verified}
    after = {entry[0]: entry for entry in current}
    changed = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old is None:
            changed.append(f"{path} (added to the batch)")
        elif new is None:
            changed.append(f"{path} (removed from the batch)")
        elif old != new:
            changed.append(f"{path} ({'operation' if old[1] != new[1] else 'content or base'} changed)")
    return changed or ["(entry identity changed)"]

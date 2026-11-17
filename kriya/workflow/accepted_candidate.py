"""S8 (C-07, ruling 9): the accepted-candidate snapshot of an enforce plan.

Under enforce a unit's accepted work lives only in the plan sandbox, and the sandbox is removed when the run stops
without committing. This module is what lets a later run reuse that work WITHOUT trusting that it exists:

* **What is persisted.** One cumulative control document (``.kriya/control/accepted_candidate.json``): per accepted
  unit, the exact text of the files that unit committed to the candidate, each file's raw digest, mode and base
  facts, and the unit's candidate binding digest - the existing S7 subject, ``bind_candidate(...).digest``, over that
  unit's writes against the real workspace. No verdict is stored: nothing here says "valid".
* **Order.** The document is durably written first; the ControlState that names each unit's digest
  (``ControlState.accepted_units``) second. Either half alone proves nothing, and a resume needs both to agree.
* **Reuse is a replay, not a restore.** A resume never copies these bytes into the candidate as accepted work. A unit
  whose entry survives every check below is handed to the unit's normal invocation as a carried candidate (the
  existing verifier-replay path): its bytes are staged by the candidate writer and judged by the unit's own gates
  again, in a fresh sandbox, with no Developer call. A unit that fails any check, or whose replay does not pass, is
  generated again like any pending unit.

The checks (``replayable_units``), each on an existing owner's identity:

1. the plan the snapshot was taken under is the current plan (the plan content hash);
2. the unit's digest in the ControlState, the unit's digest in the document and the digest RECOMPUTED from the
   document's own bytes and base facts are equal (a changed byte, mode, path or base fact changes the digest);
3. every file's base in the real workspace is still the recorded one (the file revision authority, and whether the
   file exists);
4. the files are inside the unit's current planned scope.

The plan-level identities a resume already checks before it reaches this module (requirement contract, acceptance
approval, verification contract) are not duplicated here; the enforce loop refuses the whole resume on any of them.

Not capturable, so never reused (the unit is simply generated again): a unit that deleted a file, a file that is not
strict UTF-8, a unit that wrote nothing.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.repository.commit import StagedFileWrite
from kriya.workflow.terminal_commit import (
    CandidateFile,
    CandidateMaterializationError,
    bind_candidate,
    materialize_candidate,
)
from kriya.workflow.verifier_replay import CarriedFile, RefusedCandidate

SNAPSHOT_SCHEMA_VERSION = 1

# Why a unit recorded as accepted is NOT replayed (the unit is generated again).
SNAPSHOT_MISSING = "ACCEPTED_SNAPSHOT_MISSING"                    # no readable document
SNAPSHOT_PLAN_CHANGED = "ACCEPTED_SNAPSHOT_PLAN_CHANGED"          # taken under another plan
SNAPSHOT_UNIT_MISSING = "ACCEPTED_SNAPSHOT_UNIT_MISSING"          # the ControlState names a unit the document does not hold
SNAPSHOT_REFERENCE_MISMATCH = "ACCEPTED_SNAPSHOT_REFERENCE_MISMATCH"  # the two halves name different work
SNAPSHOT_BYTES_CHANGED = "ACCEPTED_SNAPSHOT_BYTES_CHANGED"        # the recomputed binding is not the recorded one
SNAPSHOT_BASE_CHANGED = "ACCEPTED_SNAPSHOT_BASE_CHANGED"          # the real workspace is no longer the base
SNAPSHOT_OUT_OF_SCOPE = "ACCEPTED_SNAPSHOT_OUT_OF_SCOPE"          # a file outside the unit's current planned scope
SNAPSHOT_MALFORMED = "ACCEPTED_SNAPSHOT_MALFORMED"                # an entry that is not an entry


def capture_unit(
    *, paths: Sequence[str], creates: Iterable[str], candidate_root: str, workspace_path: str, base_revisions: Mapping[str, str],
) -> Optional[Dict[str, Any]]:
    """The snapshot entry of one accepted unit: the exact files it committed to the candidate and their binding digest. None when the unit's work cannot be captured
    EXACTLY (nothing written, a path with no recorded base, a file that is gone or not strict UTF-8): such a unit is not reusable, it is never carried in part."""
    creates = set(creates)
    relpaths = sorted(set(paths))
    if not relpaths or any(path not in base_revisions for path in relpaths):
        return None
    try:
        writes = materialize_candidate(candidate_root, workspace_path, [
            CandidateFile(relpath=path, expected_base_revision=base_revisions[path], expected_base_exists=path not in creates)
            for path in relpaths
        ])
    except CandidateMaterializationError:
        return None
    files: List[Dict[str, Any]] = []
    for path, write in zip(relpaths, writes, strict=True):
        data = write.content_bytes if write.content_bytes is not None else write.content.encode("utf-8")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        files.append({
            "path": path, "content": content, "mode": write.mode,
            "expected_base_revision": write.expected_base_revision, "expected_base_exists": write.expected_base_exists,
        })
    return {"digest": bind_candidate(writes, workspace_path).digest, "files": files}


def snapshot_document(plan_hash: Optional[str], units: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    return {"schema_version": SNAPSHOT_SCHEMA_VERSION, "plan_hash": plan_hash, "units": {unit: dict(units[unit]) for unit in sorted(units)}}


def _entry_writes(entry: Any, workspace_path: str) -> List[StagedFileWrite]:
    """The entry's files as the commit batch its digest was taken over. Raises on anything that is not exactly an entry."""
    if not isinstance(entry, Mapping) or not isinstance(entry.get("files"), list) or not entry["files"]:
        raise ValueError("not an entry")
    writes: List[StagedFileWrite] = []
    for item in entry["files"]:
        path, content, mode = item["path"], item["content"], item["mode"]
        revision, exists = item["expected_base_revision"], item["expected_base_exists"]
        if (not isinstance(path, str) or not path or os.path.isabs(path) or ".." in path.replace("\\", "/").split("/")
                or not isinstance(content, str) or not isinstance(revision, str) or type(exists) is not bool
                or (mode is not None and type(mode) is not int)):
            raise ValueError("not a file entry")
        target = os.path.join(workspace_path, path)
        writes.append(StagedFileWrite(
            target_path=target, content=content, base_path=target, expected_base_revision=revision,
            expected_base_exists=exists, content_bytes=content.encode("utf-8"), mode=mode,
        ))
    return writes


def replayable_units(
    document: Any, *, referenced: Mapping[str, str], plan_hash: Optional[str], workspace_path: str,
    current_revision: Callable[[str], str], planned_paths: Mapping[str, Iterable[str]],
) -> Tuple[Dict[str, RefusedCandidate], Dict[str, str]]:
    """(unit id -> its accepted work as a carried candidate, unit id -> why it is not replayed) for every unit the ControlState names. Nothing is trusted because it
    exists: each unit passes the four checks of the module docstring or is refused with its reason."""
    referenced = dict(referenced)
    if not referenced:
        return {}, {}
    if not isinstance(document, Mapping) or document.get("schema_version") != SNAPSHOT_SCHEMA_VERSION or not isinstance(document.get("units"), Mapping):
        return {}, {unit: SNAPSHOT_MISSING for unit in referenced}
    if not plan_hash or document.get("plan_hash") != plan_hash:
        return {}, {unit: SNAPSHOT_PLAN_CHANGED for unit in referenced}
    replayable: Dict[str, RefusedCandidate] = {}
    refused: Dict[str, str] = {}
    for unit, recorded_digest in sorted(referenced.items()):
        entry = document["units"].get(unit)
        if entry is None:
            refused[unit] = SNAPSHOT_UNIT_MISSING
            continue
        try:
            writes = _entry_writes(entry, workspace_path)
            entry_digest = entry["digest"]
        except (KeyError, TypeError, ValueError):
            refused[unit] = SNAPSHOT_MALFORMED
            continue
        if entry_digest != recorded_digest:
            refused[unit] = SNAPSHOT_REFERENCE_MISMATCH
            continue
        if bind_candidate(writes, workspace_path).digest != recorded_digest:
            refused[unit] = SNAPSHOT_BYTES_CHANGED
            continue
        if any(current_revision(write.target_path) != write.expected_base_revision
               or os.path.lexists(write.target_path) != write.expected_base_exists for write in writes):
            refused[unit] = SNAPSHOT_BASE_CHANGED
            continue
        relpaths = [item["path"] for item in entry["files"]]
        if not set(relpaths) <= set(planned_paths.get(unit, ())):
            refused[unit] = SNAPSHOT_OUT_OF_SCOPE
            continue
        replayable[unit] = RefusedCandidate(
            files=tuple(CarriedFile(path, write.content, hashlib.sha256(write.content_bytes or b"").hexdigest())
                        for path, write in zip(relpaths, writes, strict=True)),
            binding_digest=recorded_digest, failure_type=None, failure_digest=None,
        )
    return replayable, refused

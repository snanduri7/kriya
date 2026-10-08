"""Rejected-candidate reconstruction (REJECTED-CANDIDATE-RETENTION-001, BACKEND-READINESS-004).

The attempt-evidence recorder already retains every staged candidate path's exact bytes under capture ``full``:
each ``candidate.change`` record with decision STAGED carries the path, the attempt number and content-addressed
blobs ``before`` (the base bytes), ``after`` (the candidate bytes) and ``diff`` (the unified diff), re-hashed on
read. MEASURED on batch 003's T4-v2 FALSE_NEGATIVE run (two staged paths, three blobs each). What was missing was a
way to get them back: this module reconstructs a rejected candidate from the sealed store into an operator-chosen
directory OUTSIDE any workspace (never applied, never reinserted), with a manifest of digests, so an external oracle
can judge it exactly. Capture modes without blobs (``digests``) keep digests only, which is reported, never guessed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from kriya.core.attempt_evidence import reader

EXPORT_FORMAT = "kriya.candidate_export/1"
EXPORT_MANIFEST = "CANDIDATE_MANIFEST.json"


def staged_changes(run: reader.EvidenceRun, attempt: Optional[int] = None) -> List[Dict[str, Any]]:
    """The STAGED ``candidate.change`` records of ``run`` (the last attempt's by default)."""
    staged = [r for r in run.records()
              if r.get("kind") == "candidate.change" and (r.get("payload") or {}).get("decision") == "STAGED"]
    if not staged:
        return []
    wanted = attempt if attempt is not None else max(int(r.get("attempt_number") or 0) for r in staged)
    return [r for r in staged if int(r.get("attempt_number") or 0) == wanted]


def export_candidate(state_dir: str, run_id: str, out_dir: str, *, attempt: Optional[int] = None,
                     workspace: Optional[str] = None) -> Dict[str, Any]:
    """Write the staged candidate's ``after`` bytes under ``out_dir/<path>`` and its unified diffs under
    ``out_dir/diffs/<path>.diff``, plus ``CANDIDATE_MANIFEST.json`` (digests, attempt, run, integrity). ``out_dir``
    must lie outside ``workspace`` when one is given: rejected bytes never re-enter a repository."""
    from kriya.platform.filesystem_semantics import PathRelation, path_relation

    out_real = os.path.realpath(out_dir)
    if workspace is not None and path_relation(os.path.realpath(workspace), out_real) is not PathRelation.OUTSIDE:
        raise ValueError("the export directory must lie outside the workspace: a rejected candidate is never reinserted")
    run = reader.open_run(state_dir, run_id)
    if not run.exists:
        raise FileNotFoundError(f"no attempt-evidence store for run {run_id!r}")
    verification = run.verify()
    changes = staged_changes(run, attempt)
    os.makedirs(out_real, exist_ok=True)
    manifest: Dict[str, Any] = {"format": EXPORT_FORMAT, "run_id": run_id, "attempt": None,
                                "store_verification": getattr(verification, "status", None) or str(verification),
                                "paths": [], "without_bytes": []}
    if not changes:
        manifest["reason"] = "no staged candidate in this run (nothing was ever staged)"
        with open(os.path.join(out_real, EXPORT_MANIFEST), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True)
        return manifest
    manifest["attempt"] = int(changes[0].get("attempt_number") or 0)
    for record in changes:
        payload = record.get("payload") or {}
        path = str(payload.get("path") or "")
        blobs = record.get("blobs") or {}
        entry: Dict[str, Any] = {"path": path, "seq": record.get("seq"), "deleted": bool(payload.get("deleted")),
                                 "created": bool(payload.get("created")), "after_digest": payload.get("after_digest"),
                                 "before_digest": payload.get("before_digest"), "blobs": dict(blobs)}
        if not path or os.path.isabs(path) or ".." in path.split("/"):
            entry["problem"] = "unsafe path; not written"
            manifest["without_bytes"].append(entry)
            continue
        if "after" not in blobs and not entry["deleted"]:
            entry["problem"] = "no after-bytes blob (capture mode without blobs): digest only"
            manifest["without_bytes"].append(entry)
            continue
        target = os.path.join(out_real, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if entry["deleted"]:
            entry["written"] = None
        else:
            data = run.blob(blobs["after"])
            with open(target, "wb") as handle:
                handle.write(data)
            entry["written"] = path
            entry["bytes"] = len(data)
        if "diff" in blobs:
            diff_target = os.path.join(out_real, "diffs", path + ".diff")
            os.makedirs(os.path.dirname(diff_target), exist_ok=True)
            with open(diff_target, "wb") as handle:
                handle.write(run.blob(blobs["diff"]))
            entry["diff"] = os.path.relpath(diff_target, out_real)
        manifest["paths"].append(entry)
    with open(os.path.join(out_real, EXPORT_MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)
    return manifest

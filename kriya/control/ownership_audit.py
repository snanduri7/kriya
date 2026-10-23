"""D2 M4 (owner decision 2026-10-23): the read-only ownership audit of the workspace authority's control documents.

Every document Kriya writes into its control namespaces carries an ownership stamp naming the workspace it was written for, and every reader requires
it (``kriya.context.workspace_identity.validate_ownership``). A document without a stamp, or with another workspace's, is never adopted: there is no
migration that stamps it. ``audit_control_ownership(workspace)`` is the controller's preflight for that rule: it reads every such document ONCE, writes
nothing and returns one ``OwnershipFinding`` per document that is not provably this workspace's, so a resumed or authoritative run ends as a typed
``needs_review`` BEFORE any loader or writer touches them.

The stamp is IDENTITY hygiene (a hash of the workspace path), not authentication: a process that can write the file can write the stamp. What the
audit and the readers close is state that arrives from somewhere else (an older Kriya, a copy, a clone, an archive) and is not Kriya's own for this
workspace. A document that is not valid JSON is not an ownership question (its own loader reports it as corrupt) and is not listed here.

Namespaces: ``control/{state,contracts,artifacts}.json``, ``control/{runs,plans,commits}/*.json``, ``.kriya/checkpoints/*.json``, the milestone run-state
sidecars ``.kriya/milestones/<group>.json`` (a hand-edited ``*.plan.json`` is an input, not state) and the decision ledger (one stamp per line).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Sequence, Tuple

from kriya.context.workspace_identity import (
    WorkspaceOwnershipError,
    authority_root,
    validate_ledger_line_ownership,
    validate_ownership,
)
from kriya.control.checkpoint import CHECKPOINT_DIR
from kriya.control.control_store import MILESTONE_SIDECAR_RELATIVE_DIR
from kriya.control.decisions import decision_ledger_path
from kriya.control.persistence import (
    approved_plan_path,
    artifact_registry_path,
    contract_registry_path,
    control_state_path,
    run_record_path,
)
from kriya.repository.commit import commit_evidence_dir

_PLAN_SUFFIX = ".plan.json"


@dataclass(frozen=True)
class OwnershipFinding:
    """One control document that is not provably this workspace's: where, which closed reason code, and the rendered reason."""

    path: str
    reason_code: str
    message: str


def _json_files(directory: str, *, skip_suffix: str = "") -> Iterator[str]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return
    for name in names:
        if name.endswith(".json") and not name.startswith(".") and not (skip_suffix and name.endswith(skip_suffix)):
            yield os.path.join(directory, name)


def _document_paths(workspace_path: str) -> Tuple[str, ...]:
    authority = authority_root(workspace_path)
    paths: List[str] = [control_state_path(workspace_path), contract_registry_path(workspace_path), artifact_registry_path(workspace_path)]
    directories = (os.path.dirname(run_record_path(workspace_path, "x")), os.path.dirname(approved_plan_path(workspace_path, "x")),
                   commit_evidence_dir(workspace_path), os.path.join(authority, CHECKPOINT_DIR))
    for directory in directories:
        paths.extend(_json_files(directory))
    paths.extend(_json_files(os.path.join(authority, MILESTONE_SIDECAR_RELATIVE_DIR), skip_suffix=_PLAN_SUFFIX))
    return tuple(paths)


def _finding(path: str, error: WorkspaceOwnershipError) -> OwnershipFinding:
    return OwnershipFinding(path=path, reason_code=error.reason_code, message=str(error))


def _audit_document(workspace_path: str, path: str) -> List[OwnershipFinding]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []  # absent or corrupt: not an ownership question (its own loader reports it)
    if not isinstance(payload, dict):
        return []
    try:
        validate_ownership(workspace_path, payload, path)
    except WorkspaceOwnershipError as error:
        return [_finding(path, error)]
    return []


def _audit_ledger(workspace_path: str) -> List[OwnershipFinding]:
    path = decision_ledger_path(workspace_path)
    findings: List[OwnershipFinding] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue  # the tolerant ledger loader skips (and logs) a malformed line
                if not isinstance(payload, dict):
                    continue
                try:
                    validate_ledger_line_ownership(workspace_path, payload, f"{path} line {line_number}")
                except WorkspaceOwnershipError as error:
                    findings.append(_finding(path, error))
    except OSError:
        return []
    return findings


def audit_control_ownership(workspace_path: str) -> Tuple[OwnershipFinding, ...]:
    """Every control document of the workspace authority that carries no ownership stamp or another workspace's. Read-only; empty means every
    document that is a JSON object is stamped for this workspace."""
    findings: List[OwnershipFinding] = []
    for path in _document_paths(workspace_path):
        findings.extend(_audit_document(workspace_path, path))
    findings.extend(_audit_ledger(workspace_path))
    return tuple(findings)


def refusal_fields(findings: Sequence[OwnershipFinding]) -> Dict[str, Any]:
    """The result fields of the typed ``needs_review`` refusal for ``findings``: the closed reason codes, every document named, the first reason rendered."""
    return {
        "reason_codes": sorted({finding.reason_code for finding in findings}),
        "ownership_findings": [{"path": finding.path, "reason_code": finding.reason_code} for finding in findings],
        "message": findings[0].message,
    }

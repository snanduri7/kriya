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
from typing import Any, Dict, Optional

from kriya.control.artifacts import ArtifactRegistry
from kriya.control.contracts import ContractRegistry
from kriya.control.state import ControlState
from kriya.control.workspace_identity import WorkspaceOwnershipError, ownership_metadata, validate_ownership
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow.edit_safety import read_file_revision

logger = logging.getLogger(__name__)

_CONTROL_DIR = os.path.join(".kriya", "control")
_STATE_FILENAME = "state.json"
_CONTRACTS_FILENAME = "contracts.json"
_ARTIFACTS_FILENAME = "artifacts.json"
_DECISIONS_FILENAME = "decisions.jsonl"
_APPROVED_PLANS_DIRNAME = "plans"


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


def save_approved_plan(workspace_path: str, plan_id: str, payload: Dict[str, Any]) -> None:
    """Atomically persist a validated plan and its execution-stage state."""
    _save_json_document(workspace_path, approved_plan_path(workspace_path, plan_id), payload)


def load_approved_plan(workspace_path: str, plan_id: str) -> Optional[Dict[str, Any]]:
    """Load a validated plan document, enforcing workspace ownership."""
    return _load_json_document(approved_plan_path(workspace_path, plan_id), workspace_path)


def _save_json_document(workspace_path: str, path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    owned_payload = dict(payload)
    owned_payload["_workspace"] = ownership_metadata(workspace_path)
    content = json.dumps(owned_payload, indent=2, sort_keys=True)
    expected_revision = read_file_revision(path)
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
    try:
        return ArtifactRegistry.from_dict(data)
    except Exception:
        logger.warning("Failed to reconstruct ArtifactRegistry from %s - starting empty", artifact_registry_path(workspace_path), exc_info=True)
        return ArtifactRegistry()

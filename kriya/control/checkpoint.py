"""The checkpoint store (full modularization D2; MODULE-CONTRACTS §6 ``CheckpointStore``): STATE owns where a resume checkpoint lives,
how it is written, read, listed, deleted and which RunRecord it references (PRD-008 retention), and the execution-identity
fingerprints of configuration and control-plane registries it records.

Moved verbatim from ``kriya/workflow/checkpoint.py`` (bodies byte-identical, same files and formats): checkpoints are written
incrementally to ``.kriya/checkpoints/<run_id>.json`` inside the target workspace and deleted on normal completion - they only survive
on disk if the process is killed or crashes mid-run. The git/working-tree fingerprints and the resume-vs-reality validation stay in the
kernel module (the git helpers move to ``kriya/repository`` in M2); ``kriya/workflow/checkpoint.py`` re-exports every name moved here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from kriya.context.workspace_identity import WorkspaceOwnershipError, ownership_metadata, validate_ownership

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = os.path.join(".kriya", "checkpoints")


def _checkpoints_dir(workspace_path: str) -> str:
    return os.path.join(workspace_path, CHECKPOINT_DIR)


def checkpoint_path(workspace_path: str, run_id: str) -> str:
    return os.path.join(_checkpoints_dir(workspace_path), f"{run_id}.json")


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


# Configuration that only decides what Kriya RECORDS, never what it does
# (LR-R1-M1 invariant I-2): excluded from every execution-identity
# fingerprint, so a capture-mode change is never config drift. SEC-009
# approval digests still cover it (kriya/config/authority_approval.py).
OBSERVATIONAL_CONFIG_SECTIONS = ("evidence",)


def without_observational_config(config_dict: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in config_dict.items() if key not in OBSERVATIONAL_CONFIG_SECTIONS}


def compute_config_fingerprint(config_dict: Dict[str, Any]) -> str:
    blob = json.dumps(without_observational_config(config_dict), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def save_checkpoint(workspace_path: str, run_id: str, data: Dict[str, Any]) -> None:
    d = _checkpoints_dir(workspace_path)
    try:
        os.makedirs(d, exist_ok=True)
        path = checkpoint_path(workspace_path, run_id)
        tmp_path = path + ".tmp"
        payload = dict(data)
        payload["run_id"] = run_id
        payload["saved_at"] = time.time()
        payload["_workspace"] = ownership_metadata(workspace_path)
        from kriya.control.run_coordinator import current_run_context
        context = current_run_context()
        if context is not None and context.record_revision is not None:
            payload["_run_record"] = {
                "classification": "derived",
                "run_id": context.run_id,
                "revision": context.record_revision,
            }
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, path)
        logger.info(f"Checkpoint saved (run_id={run_id}, stage={data.get('stage')}): {path}")
    except Exception as e:
        logger.warning(f"Failed to save checkpoint '{run_id}' (non-fatal, resume for this run won't be available): {e}")


def load_checkpoint(workspace_path: str, run_id: str) -> Optional[Dict[str, Any]]:
    path = checkpoint_path(workspace_path, run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        validate_ownership(workspace_path, payload, path)
        return payload
    except WorkspaceOwnershipError:
        raise
    except Exception as e:
        logger.warning(f"Failed to load checkpoint '{path}': {e}")
        return None


def delete_checkpoint(workspace_path: str, run_id: str) -> None:
    path = checkpoint_path(workspace_path, run_id)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.debug(f"Failed to delete checkpoint '{path}' (non-fatal): {e}")


def list_checkpoints(workspace_path: str) -> List[Dict[str, Any]]:
    d = _checkpoints_dir(workspace_path)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            data = load_checkpoint(workspace_path, fn[:-len(".json")])
            if data:
                out.append(data)
    return out


def find_latest_checkpoint(workspace_path: str) -> Optional[str]:
    checkpoints = list_checkpoints(workspace_path)
    if not checkpoints:
        return None
    checkpoints.sort(key=lambda c: c.get("saved_at", 0))
    return checkpoints[-1]["run_id"]


def list_checkpoint_run_references(workspace_path: str) -> List[str]:
    """Run ids whose RunRecord a saved checkpoint references (PRD-008
    retention protects them). A checkpoint that cannot be loaded is one
    resume will not use, so its reference does not need protecting."""
    d = _checkpoints_dir(workspace_path)
    if not os.path.isdir(d):
        return []
    references = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            data = load_checkpoint(workspace_path, fn[:-len(".json")])
        except WorkspaceOwnershipError:
            continue
        reference = (data or {}).get("_run_record")
        if isinstance(reference, dict) and reference.get("run_id"):
            references.append(str(reference["run_id"]))
    return references


def compute_registry_hash(registry_dict: Dict[str, Any]) -> str:
    """Stable sha256 over a control-plane registry's own to_dict() -
    reused for both contract_hash and artifact_registry_hash below (each
    registry's to_dict() already has a well-defined, stable shape - see
    kriya/control/contracts.py::ContractRegistry.to_dict()/kriya/control/
    artifacts.py::ArtifactRegistry.to_dict())."""
    blob = json.dumps(registry_dict, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


__all__ = [
    "CHECKPOINT_DIR", "OBSERVATIONAL_CONFIG_SECTIONS", "checkpoint_path", "compute_config_fingerprint", "compute_registry_hash",
    "delete_checkpoint", "find_latest_checkpoint", "list_checkpoint_run_references", "list_checkpoints", "load_checkpoint", "new_run_id",
    "save_checkpoint", "without_observational_config",
]

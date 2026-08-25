"""Stage-level checkpoint/resume support for interrupted generate/fix runs.

Checkpoints are written incrementally to `.kriya/checkpoints/<run_id>.json` inside
the target workspace and deleted on normal completion - they only survive on disk
if the process is killed or crashes mid-run. Resume is opt-in only (an explicit
--resume/--resume-id CLI flag); there is no auto-detection from goal text. Drift is
checked strictly: any difference in the workspace git HEAD/dirty-state, the
resolved config, or the goal/error text invalidates the checkpoint entirely rather
than attempting a partial/best-effort resume.
"""
import hashlib
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from kriya.control.workspace_identity import WorkspaceOwnershipError, ownership_metadata, validate_ownership

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = os.path.join(".kriya", "checkpoints")


def _checkpoints_dir(workspace_path: str) -> str:
    return os.path.join(workspace_path, CHECKPOINT_DIR)


def checkpoint_path(workspace_path: str, run_id: str) -> str:
    return os.path.join(_checkpoints_dir(workspace_path), f"{run_id}.json")


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def compute_workspace_fingerprint(workspace_path: str) -> Optional[str]:
    """Git HEAD SHA + dirty/clean marker. None if not a git repo (resume unavailable)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace_path, capture_output=True, text=True
        )
        if head.returncode != 0:
            return None
        # Exclude .kriya/ (checkpoints, worktree sandbox) from the dirty check -
        # writing a checkpoint file is itself an untracked change under .kriya/,
        # which would otherwise make every workspace look "dirty" the moment a
        # checkpoint is saved and permanently block resuming it.
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!.kriya"],
            cwd=workspace_path, capture_output=True, text=True
        )
        dirty = "dirty" if status.stdout.strip() else "clean"
        return f"{head.stdout.strip()}:{dirty}"
    except Exception as e:
        logger.debug(f"Failed to compute workspace fingerprint for '{workspace_path}': {e}")
        return None


def compute_config_fingerprint(config_dict: Dict[str, Any]) -> str:
    blob = json.dumps(config_dict, sort_keys=True, default=str)
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


# --- MA5.9: control-plane hash bundle + resume-vs-reality validation ---
#
# save_checkpoint() itself is untouched (still a plain Dict[str, Any] - "Do
# not require all fields for legacy checkpoints" is automatically true for a
# schemaless dict). compute_control_plane_hashes() below produces the
# additive hash fields section 29 calls for; a real caller merges its
# result into whatever dict it already passes to save_checkpoint(), e.g.
# `save_checkpoint(ws, run_id, {**existing_data, **compute_control_plane_hashes(...)})`.
# Nothing here changes run_generation_workflow()'s own existing resume/
# drift-check block (workspace/config/goal fingerprints) - this is a
# SEPARATE, additional validation layer a caller opts into, not a
# replacement of the one that already exists and is already load-bearing.

CONTROL_PLANE_CHECKPOINT_SCHEMA_VERSION = 1


def compute_tree_hash(workspace_path: str) -> Optional[str]:
    """git's own tree object hash (`HEAD^{tree}`) - distinct from the
    commit SHA compute_base_commit returns: a tree hash changes only when
    real file CONTENT changes, so it stays stable across an amend/rebase
    that doesn't actually touch content, while still catching any real
    drift a bare commit-SHA comparison might miss in an unusual history
    rewrite. None if not a git repo or git is unavailable - same fail-
    closed shape compute_workspace_fingerprint above already uses."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=workspace_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Failed to compute tree hash for '{workspace_path}': {e}")
        return None


def compute_base_commit(workspace_path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Failed to compute base commit for '{workspace_path}': {e}")
        return None


def compute_registry_hash(registry_dict: Dict[str, Any]) -> str:
    """Stable sha256 over a control-plane registry's own to_dict() -
    reused for both contract_hash and artifact_registry_hash below (each
    registry's to_dict() already has a well-defined, stable shape - see
    kriya/control/contracts.py::ContractRegistry.to_dict()/kriya/control/
    artifacts.py::ArtifactRegistry.to_dict())."""
    blob = json.dumps(registry_dict, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_control_plane_hashes(
    workspace_path: str,
    control_state: Optional[Any] = None,
    contract_registry: Optional[Any] = None,
    artifact_registry: Optional[Any] = None,
    context_package: Optional[Any] = None,
    plan_hash: Optional[str] = None,
    verification_hash: Optional[str] = None,
    patch_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Bundles every MA5 checkpoint hash field (section 29) plus
    control_state_hash (needed for section 30's own "validate control-state
    hash" step, implied by but not itself named in section 29's field list)
    into one dict ready to merge into a checkpoint's own data. Every
    argument is optional and independently None-safe - a caller mid-run
    that doesn't yet have, say, a ContextPackage built simply omits
    context_package_hash from the result rather than the whole bundle
    failing. Types are accepted as Any (not kriya.control.state.ControlState
    etc. directly) so this module doesn't need a hard import dependency on
    kriya/control/ for a caller that only wants the git-derived fields."""

    return {
        "schema_version": CONTROL_PLANE_CHECKPOINT_SCHEMA_VERSION,
        "base_commit": compute_base_commit(workspace_path),
        "tree_hash": compute_tree_hash(workspace_path),
        "patch_hash": patch_hash,
        "verification_hash": verification_hash,
        "plan_hash": plan_hash,
        "contract_hash": compute_registry_hash(contract_registry.to_dict()) if contract_registry is not None else None,
        "artifact_registry_hash": compute_registry_hash(artifact_registry.to_dict()) if artifact_registry is not None else None,
        "context_package_hash": context_package.package_hash if context_package is not None else None,
        "control_state_hash": control_state.content_hash() if control_state is not None else None,
    }


class ResumeStatus(str, Enum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class ResumeValidationResult:
    status: ResumeStatus
    mismatches: Tuple[str, ...] = ()


def validate_resume_against_reality(
    checkpoint_data: Dict[str, Any],
    workspace_path: str,
    control_state: Optional[Any] = None,
    contract_registry: Optional[Any] = None,
    artifact_registry: Optional[Any] = None,
) -> ResumeValidationResult:
    """Section 30's flow: validate checkpoint commit -> validate tree hash
    -> validate control-state hash -> validate contract/artifact registry
    hashes -> NEEDS_REVIEW on any mismatch, never silently rebuilt.
    "Re-run required acceptance" and the actual resume/abort decision are
    the CALLER's job (this is a pure comparison, no side effects) - see
    this module's own docstring for why this is additive to, not a
    replacement of, run_generation_workflow()'s existing workspace/config/
    goal drift check.

    Each individual check only fires when BOTH the checkpoint stored that
    hash AND the caller supplied the matching live object to compare
    against - "do not require all fields for legacy checkpoints" (section
    29) means an old checkpoint or a caller that doesn't have, say, a
    ContractRegistry yet simply skips that one check, not a mismatch."""

    mismatches: List[str] = []

    stored_base_commit = checkpoint_data.get("base_commit")
    if stored_base_commit is not None:
        current = compute_base_commit(workspace_path)
        if current != stored_base_commit:
            mismatches.append(f"base_commit: checkpoint={stored_base_commit!r} current={current!r}")

    stored_tree_hash = checkpoint_data.get("tree_hash")
    if stored_tree_hash is not None:
        current = compute_tree_hash(workspace_path)
        if current != stored_tree_hash:
            mismatches.append(f"tree_hash: checkpoint={stored_tree_hash!r} current={current!r}")

    stored_control_hash = checkpoint_data.get("control_state_hash")
    if stored_control_hash is not None and control_state is not None:
        current = control_state.content_hash()
        if current != stored_control_hash:
            mismatches.append("control_state_hash mismatch")

    stored_contract_hash = checkpoint_data.get("contract_hash")
    if stored_contract_hash is not None and contract_registry is not None:
        current = compute_registry_hash(contract_registry.to_dict())
        if current != stored_contract_hash:
            mismatches.append("contract_hash mismatch")

    stored_artifact_hash = checkpoint_data.get("artifact_registry_hash")
    if stored_artifact_hash is not None and artifact_registry is not None:
        current = compute_registry_hash(artifact_registry.to_dict())
        if current != stored_artifact_hash:
            mismatches.append("artifact_registry_hash mismatch")

    status = ResumeStatus.NEEDS_REVIEW if mismatches else ResumeStatus.OK
    return ResumeValidationResult(status=status, mismatches=tuple(mismatches))

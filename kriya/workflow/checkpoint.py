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
from typing import Any, Dict, List, Optional

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
            return json.load(fh)
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

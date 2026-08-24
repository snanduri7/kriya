"""ControlState persistence - MA5.1 of the control-plane implementation
plan. Persists to `.kriya/control/state.json` inside the target workspace,
one file per workspace (not per run_id - a workspace has exactly one
current control state, the same way it has exactly one worktree).

The write goes through kriya/policy/filesystem.py's AuthorizedFileWriter
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
from typing import Optional

from kriya.control.state import ControlState
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow.edit_safety import read_file_revision

logger = logging.getLogger(__name__)

_CONTROL_DIR = os.path.join(".kriya", "control")
_STATE_FILENAME = "state.json"


def control_state_path(workspace_path: str) -> str:
    return os.path.join(workspace_path, _CONTROL_DIR, _STATE_FILENAME)


def save_control_state(workspace_path: str, state: ControlState) -> None:
    path = control_state_path(workspace_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = json.dumps(state.to_dict(), indent=2, sort_keys=True)
    expected_revision = read_file_revision(path)
    AuthorizedFileWriter(workspace_path).commit_file(path, content, expected_revision=expected_revision)


def load_control_state(workspace_path: str) -> Optional[ControlState]:
    """None if no control state has ever been saved for this workspace, or
    if the file exists but can't be parsed (fails closed - a corrupt
    control-state file must never crash the caller or silently be treated
    as an empty-but-valid state; the caller decides how to proceed with
    None, e.g. constructing a fresh ControlState.new())."""

    path = control_state_path(workspace_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ControlState.from_dict(data)
    except Exception:
        logger.warning("Failed to load control state at %s - treating as absent", path, exc_info=True)
        return None

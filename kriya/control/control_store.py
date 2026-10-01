"""The one writer for Kriya's own workspace control state (PLAT-039).

Kriya keeps run control state under ``<workspace>/.kriya/``: RunRecords,
the decision ledger, the contract registry, milestone sidecars, planning
diagnostics. Candidate (model-directed) writes go through
``kriya.policy.filesystem.AuthorizedFileWriter``, which refuses every
trusted control path; Kriya's own stores write here instead, and only
beneath ``.kriya/``. The two authorities never overlap: candidate authority
cannot reach control state, and this writer cannot reach repository files.
"""
from __future__ import annotations

import os

from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.workflow.edit_safety import commit_revision_grounded_file

CONTROL_DIRECTORY = ".kriya"


class ControlStorePathError(ValueError):
    """A control-state write whose target is not beneath ``.kriya/``."""


def write_control_file(workspace_path: str, path: str, content: str, *, expected_revision: str) -> str:
    """Atomically write ``content`` to ``path`` beneath the workspace's
    ``.kriya/`` directory if its base revision is unchanged; returns the new
    revision. A target outside ``.kriya/`` (or whose location cannot be
    established) is refused before anything is written."""
    control_root = os.path.join(workspace_path, CONTROL_DIRECTORY)
    if path_relation(control_root, path) is not PathRelation.WITHIN:
        raise ControlStorePathError(f"control-state write outside {control_root!r} refused: {path!r}")
    return commit_revision_grounded_file(path, content, expected_revision=expected_revision,
                                         workspace_path=workspace_path)

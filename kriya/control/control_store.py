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
from typing import Any, Optional, Tuple

from kriya.context.workspace_identity import authority_root
from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.platform.private_store import ensure_private_directories, ensure_private_directory
from kriya.repository.commit import commit_revision_grounded_file

CONTROL_DIRECTORY = ".kriya"
# The milestone sidecars (kriya/workflow/milestones.py writes them; retention reads their commit ledgers): moved here from
# kriya/workflow/milestone_completion.py in D2 so the one path is STATE's.
MILESTONE_SIDECAR_RELATIVE_DIR = os.path.join(".kriya", "milestones")


class ControlStorePathError(ValueError):
    """A control-state write whose target is not beneath ``.kriya/``."""


def write_control_file(workspace_path: str, path: str, content: str, *, expected_revision: str, durable: bool = False, content_bytes: Optional[bytes] = None) -> str:
    """Atomically write ``content`` to ``path`` beneath the workspace's
    ``.kriya/`` directory if its base revision is unchanged; returns the new
    revision. A target outside ``.kriya/`` (or whose location cannot be
    established) is refused before anything is written."""
    # D-001: the control stores live under the AUTHORITY root's ``.kriya/`` (the repository's), the invoked directory's own ``.kriya/`` holds only
    # that directory's scratch; a control write is legitimate beneath either, never elsewhere.
    control_root, authority_control_root = control_roots(workspace_path)
    if path_relation(control_root, path) is not PathRelation.WITHIN and path_relation(authority_control_root, path) is not PathRelation.WITHIN:
        raise ControlStorePathError(f"control-state write outside {control_root!r} refused: {path!r}")
    # WAVE8 OI-134/154: control state (RunRecords, control state, decisions, milestones, diagnostics) can hold a credential the redactor does not own (digest-bound
    # bytes): the control directories are private (0700) and the file is created 0600 (kriya/platform/private_store.py); another owner's artifact refuses.
    ensure_private_control_directories(workspace_path, path)
    return commit_revision_grounded_file(path, content, expected_revision=expected_revision,
                                         workspace_path=workspace_path, content_bytes=content_bytes, durable=durable, private=True)


def control_roots(workspace_path: str) -> Tuple[str, str]:
    """(the invoked directory's own ``.kriya``, the AUTHORITY root's ``.kriya``): the only two places a control write is legitimate."""
    return (os.path.join(workspace_path, CONTROL_DIRECTORY), os.path.join(authority_root(workspace_path), CONTROL_DIRECTORY))


def ensure_private_control_directories(workspace_path: str, path: str) -> None:
    """Every directory from the owning ``.kriya`` down to ``path``'s parent is private. ``path`` is already known to be beneath a control root."""
    for base in control_roots(workspace_path):
        if path_relation(base, path) is PathRelation.WITHIN:
            ensure_private_directories(base, os.path.dirname(path))
            return


def ensure_private_control_roots(workspace_path: str) -> None:
    """The authority root's ``.kriya`` and its ``control`` / ``checkpoints`` directories are private (created when missing) - called at the start of a run."""
    anchor = control_roots(workspace_path)[1]
    for leaf in ("control", "checkpoints"):
        ensure_private_directories(anchor, os.path.join(anchor, leaf))


def ensure_private_roots(workspace_path: str, cfg: Any) -> None:
    """The private roots of a run (WAVE8 OI-134 / OI-154, OBS-003): where Kriya keeps material that can hold a credential or security-authority state.
    Called at the start of every run, so each store - including those that write their own temp + replace - sits under a directory only this user can
    enter. Mechanism: kriya/platform/private_store.py (0700 directories, 0600 files, tighten THIS user's legacy artifacts, refuse another owner's);
    this function only NAMES the roots, each through its canonical resolver: Kriya's home (``~/.kriya``, only tightened when it exists), the authority home,
    the state directory, and the workspace's ``.kriya`` with ``control`` / ``checkpoints``. Raises ``PrivateStoreUnsafeError`` (a symlink or another owner's
    directory): a run must not start on a store Kriya does not own."""
    from kriya.config.environment import kriya_private_homes
    from kriya.core.state_paths import resolve_state_directory

    home, authority = kriya_private_homes()
    state = resolve_state_directory(cfg)[0]
    # Kriya's home is tightened only when Kriya's own state actually lives under it (the default layout): a run whose state and authority homes are
    # elsewhere (an operator override, a test) does not reach into a home directory it does not use. It is validated FIRST (WAVE8 private-store ordering):
    # an existing home that is a symlink or another owner's directory refuses before anything is created beneath it - a mkdir below a symlinked home would
    # create directories inside whatever the link names, a location this user never validated. A missing home is created private as the parent of the state.
    if any(path_relation(home, path) is PathRelation.WITHIN for path in (authority, state)):
        ensure_private_directory(home, create=False)
    for path in (authority, state):
        ensure_private_directory(path)
    ensure_private_control_roots(workspace_path)

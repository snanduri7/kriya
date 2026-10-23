"""REPOSITORY: refusal of Kriya runtime/control state that a repository CARRIES (D2 M3, owner decision 2026-10-23).

Kriya's run-control state (``.kriya/control``, ``.kriya/checkpoints``, the milestone run-state sidecars, ``.kriya/run.lock``) is created by
Kriya on THIS machine, by a run. It is workspace-resident and carries no authentication (the ownership stamp is an identity hash of the
path), so a repository that TRACKS such files - a ``git clone``, a ``git checkout`` of another branch, a ``git pull`` - decides what Kriya
later reads as having happened here: a pre-decided human approval, a "completed" milestone, an unsettled commit, a persisted command that
replay would execute (MEASURED, D2 characterization: every family travels through ``git add -f`` + ``git clone`` and is accepted once its
stamp is removed). Files that arrive by version control are never Kriya's own state.

``refuse_repository_carried_control_state(root)`` raises :class:`RepositoryCarriesControlStateRefused` when the index of the repository at
``root`` tracks any file in those namespaces, and also when that cannot be determined (fail closed: an uninspected repository does not
start a run). It is called at mutating-run entry (``begin_mutating_run``), after the content-filter refusal and before the workspace lock.

Scope, deliberately narrow: only the runtime/control namespaces above. Intentionally tracked static inputs are NOT refused: ``kriya.yaml``,
a reviewed milestone PLAN (``.kriya/milestones/<group>.plan.json`` is the hand-edited input of ``generate --from-milestones``,
docs/user_guide.md), and the repository-resident policy manifest ``.kriya/policy/approved-sources.json``.

Identity hygiene and placement are different protections: this refusal closes the version-control carrier only. It does not protect against
a process that writes the files directly (that is the execution layer's concern).
"""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Sequence

from kriya.foundation.redaction import redact_text
from kriya.repository.git import tracked_paths

REPOSITORY_CARRIES_CONTROL_STATE = "REPOSITORY_CARRIES_CONTROL_STATE"
CARRIED_STATE_UNINSPECTABLE = "CARRIED_STATE_UNINSPECTABLE"

# The one closed table of this module's reason codes; the message is rendered from it only.
CARRIED_CONTROL_REFUSALS: Dict[str, str] = {
    REPOSITORY_CARRIES_CONTROL_STATE: (
        "the repository at {repository} tracks Kriya runtime/control state ({paths}). Kriya's run state is created by Kriya on this machine "
        "and is never read from repository content: a repository that carries it could decide what Kriya believes happened here. Remove it "
        "from version control (git rm --cached -r <path>) and run again. Nothing was read, written or run."
    ),
    CARRIED_STATE_UNINSPECTABLE: (
        "Kriya could not determine whether the repository at {repository} tracks runtime/control state ({detail}); it does not start a run "
        "on a repository it could not inspect. Nothing was read, written or run."
    ),
}

# Git pathspecs (relative to the repository top) of the Kriya-owned runtime/control namespaces.
RUNTIME_CONTROL_PATHSPECS = (".kriya/control", ".kriya/checkpoints", ".kriya/milestones", ".kriya/run.lock")
# A tracked file inside a runtime namespace that is an INPUT, not state: the hand-edited milestone plan.
_PLAN_DIRECTORY = ".kriya/milestones/"
_PLAN_SUFFIX = ".plan.json"
_REPORTED_PATHS = 5


class RepositoryCarriesControlStateRefused(Exception):
    """A typed refusal: the repository tracks Kriya runtime/control state, or that could not be determined. ``str(error)`` is
    ``"<REASON_CODE>: <message>"`` (redacted). Deliberately not a RuntimeError/OSError/ValueError subclass, so no handler written
    for an ordinary git failure absorbs it (the same contract as ``RepositoryContentFilterRefused``)."""

    def __init__(self, reason_code: str, repository: str, *, paths: Sequence[str] = (), detail: str = "") -> None:
        self.reason_code = reason_code
        self.repository = repository
        self.paths = tuple(paths)
        shown = ", ".join(self.paths[:_REPORTED_PATHS]) + (f" and {len(self.paths) - _REPORTED_PATHS} more" if len(self.paths) > _REPORTED_PATHS else "")
        message = CARRIED_CONTROL_REFUSALS[reason_code].format(repository=repository, paths=shown, detail=detail)
        super().__init__(redact_text(f"{reason_code}: {message}"))


def _is_static_input(path: str) -> bool:
    return path.startswith(_PLAN_DIRECTORY) and path.endswith(_PLAN_SUFFIX)


def refuse_repository_carried_control_state(root: str) -> None:
    """Raise :class:`RepositoryCarriesControlStateRefused` when the repository whose top is ``root`` tracks runtime/control state or cannot
    be inspected; return otherwise. A ``root`` that is not the top of a Git work tree (no ``.git``) carries nothing."""
    top = os.fspath(root)
    if not os.path.lexists(os.path.join(top, ".git")):
        return
    try:
        tracked: List[str] = tracked_paths(top, RUNTIME_CONTROL_PATHSPECS)
    except subprocess.TimeoutExpired as error:
        raise RepositoryCarriesControlStateRefused(CARRIED_STATE_UNINSPECTABLE, top, detail=f"git did not answer within {error.timeout} s") from error
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RepositoryCarriesControlStateRefused(CARRIED_STATE_UNINSPECTABLE, top, detail=f"{type(error).__name__}: {error}") from error
    carried = sorted(path for path in tracked if not _is_static_input(path))
    if carried:
        raise RepositoryCarriesControlStateRefused(REPOSITORY_CARRIES_CONTROL_STATE, top, paths=carried)

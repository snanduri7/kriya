"""OI-142 (owner decision Q4 2026-10-20, design approval 2026-10-23): every verification gate runs in a FRESH, Kriya-owned view of the
authoritative bound tree, outside the persistent worktree, and the view is discarded afterwards.

Why. The gate executes candidate-controlled code (a test, the app, a build). Run in the shared, persistent, read-write worktree, it
also *reads* whatever ignored content an earlier gate (or anything else that ran there) left behind - a ``__pycache__/*.pyc``, a
sourceless ``.pyc``, an ignored ``conftest.py`` / ``pytest.ini``, an ignored module or symlink - none of which the verification tree
binding covers (FILE-INTEGRITY-CONTRACT-001B: ignored output is never repository content) and none of which ``git status`` shows. The
verdict then describes a tree nobody bound. A gate consumes only (A) the authoritative candidate bytes - tracked content, untracked
non-ignored content and the candidate paths - and (B) explicitly declared, provenance-bound artifacts; ignored residue is neither.

What the view holds: exactly the bound set, byte for byte and mode for mode (symlinks stay links, nothing is hard-linked, ``.git`` and
``.kriya`` are not copied). After the copy every path's content identity is compared with the identity it was bound to; a difference is
a typed refusal, never a run on a different tree. Failure to build the view is a typed refusal too (``GateViewError``, a
``ContainmentSetupError``: the run is refused, never run in place) - there is no fallback to the shared worktree.

What is NOT here: the choice of which gates use a view, the declared artifacts a gate imports/exports, the execution-environment
inputs (venv, dependency caches) and the 001/001B checks of the view. Those belong to the validator (kriya/tools/validate.py) and the
tree binding (kriya/workflow/file_integrity.py); this module only builds, guards and removes views.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from kriya.config.environment import KRIYA_DIRECTORY_NAME
from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.repository.snapshot import _content_identity, _stat_identity, visible_repository_paths
from kriya.tools.containment import ContainmentSetupError

logger = logging.getLogger(__name__)

GATE_VIEW_UNAVAILABLE = "GATE_VIEW_UNAVAILABLE"
GATE_VIEW_IDENTITY_MISMATCH = "GATE_VIEW_IDENTITY_MISMATCH"
GATE_VIEW_PATH_CONFUSION = "GATE_VIEW_PATH_CONFUSION"
GATE_VIEW_REASON_CODES = frozenset({GATE_VIEW_UNAVAILABLE, GATE_VIEW_IDENTITY_MISMATCH, GATE_VIEW_PATH_CONFUSION})

_VIEWS_DIRECTORY = "gate-views"
_NEVER_COPIED = (".git", KRIYA_DIRECTORY_NAME)


class GateViewError(ContainmentSetupError):
    """A verification gate could not be given a trustworthy view of the bound tree. ``ProcessController``'s sibling refusal: the
    command is not run, and it is never run in the shared worktree instead. ``reason_code`` is one of GATE_VIEW_REASON_CODES."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


@dataclass
class GateView:
    """One gate's fresh view: ``root`` is where the gate executes, ``origin`` the (real) tree it is a view of."""

    root: str
    origin: str  # the real path of the tree this is a view of
    workspace_path: str  # the same tree as the validator spelled it (restored when the view is closed)
    environment_roots: Tuple[str, ...] = ()  # execution-environment inputs hoisted outside the view, inside the origin (the project venv)
    output_roots: Set[str] = field(default_factory=set)  # view-relative toolchain output roots the gate's commands registered
    expected: Dict[str, Optional[str]] = field(default_factory=dict)  # relpath -> the content identity the view was proven to hold
    stats: Dict[str, Optional[tuple]] = field(default_factory=dict)  # relpath -> its lstat identity right after the copy

    def changed_paths(self) -> List[str]:
        """The copied paths whose content now differs from what the view was proven to hold: what the gate (or the code it ran) rewrote,
        replaced or removed in the tree it executed in. The stat identity decides what is worth re-hashing."""
        changed = []
        for relpath, identity in self.expected.items():
            path = os.path.join(self.root, relpath)
            if _stat_identity(path) != self.stats.get(relpath) and _content_identity(path) != identity:
                changed.append(relpath)
        return sorted(changed)

    def refers_to_origin(self, token: str) -> bool:
        """Whether ``token`` (an argv element or a cwd) names the origin tree or something in it - an absolute path the gate
        would resolve to the shared worktree instead of its own view. Decided by filesystem identity (a case or symlink alias of the
        origin is the origin), and an undecidable relation counts as naming it: the refusal fails closed."""
        if not os.path.isabs(token) or any(path_relation(root, token) is PathRelation.WITHIN for root in self.environment_roots):
            return False
        return path_relation(self.origin, token) is not PathRelation.OUTSIDE

    def close(self) -> None:
        """Remove the view. A view that cannot be removed is left behind and logged: cleanup is never part of a gate's truth, so it
        must not turn a verdict into an error or replace the gate's own exception."""
        try:
            _remove_tree(self.root)
        except Exception as error:  # pylint: disable=broad-except
            logger.warning("Gate view %s could not be removed (%s); it is left in place", self.root, error)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_ROOTS.discard(self.root)


_ACTIVE_ROOTS: Set[str] = set()  # view roots open in this process
_ACTIVE_LOCK = threading.Lock()


def _remove_tree(path: str) -> None:
    for directory, _dirnames, _filenames in os.walk(path):
        os.chmod(directory, 0o700)  # a gate may have made its own directories read-only; they are still Kriya's to remove
    shutil.rmtree(path, ignore_errors=True)


def _claim_root(base: str, origin: str) -> str:
    """The directory for one view of ``origin``: STABLE for that tree (``view-<hash of its real path>``), because a gate's output names the
    directory it ran in and the retry loop compares failures between attempts - a fresh random name per gate would make identical failures
    look distinct. A directory a failed cleanup left behind is Kriya's own and is replaced; one open in this process gets a numbered sibling."""
    key = hashlib.sha256(os.path.realpath(origin).encode("utf-8")).hexdigest()[:16]
    number = 0
    while True:
        path = os.path.join(base, f"view-{key}" + (f"-{number}" if number else ""))
        with _ACTIVE_LOCK:
            if path not in _ACTIVE_ROOTS:
                if os.path.lexists(path):
                    _remove_tree(path)
                try:
                    os.makedirs(path, mode=0o700)
                except FileExistsError:  # could not be removed: another sibling
                    number += 1
                    continue
                _ACTIVE_ROOTS.add(path)
                return os.path.realpath(path)
        number += 1


def views_directory(state_root: str) -> str:
    """Where views live: under the Kriya state root (never inside a worktree or an owner workspace). The state root is resolved by its
    owner and handed in."""
    return os.path.join(os.path.realpath(state_root), _VIEWS_DIRECTORY)


def is_git_work_tree(root: str) -> bool:
    """Whether ``root`` is inside a Git work tree: a ``.git`` entry (directory, or the file of a linked work tree) at ``root`` or any
    ancestor. Decided on the filesystem, not by spawning Git, so a directory that is no repository costs no process."""
    directory = os.path.realpath(root)
    while True:
        if os.path.lexists(os.path.join(directory, ".git")):
            return True
        parent = os.path.dirname(directory)
        if parent == directory:
            return False
        directory = parent


def build_gate_view(origin: str, *, state_root: str, manifest: Optional[Mapping[str, Optional[str]]] = None,
                    extra_paths: Iterable[str] = (), environment_roots: Tuple[str, ...] = ()) -> GateView:
    """Copy the bound set of ``origin`` into a fresh directory and prove the copy.

    ``manifest`` (relpath -> the content identity the path was bound to) comes from the tree binding; every manifest path is
    verified against it after the copy. Without a binding the set is Git's own view of ``origin`` and each path is verified
    against the identity it had in ``origin`` immediately before it was copied. ``extra_paths`` are bound-but-unidentified
    paths (untracked non-ignored content that existed at binding time)."""
    expected: dict = dict(manifest) if manifest is not None else {}
    paths = set(expected) | set(extra_paths)
    if manifest is None:
        try:
            paths |= set(visible_repository_paths(origin))
        except RuntimeError as error:
            raise GateViewError(GATE_VIEW_UNAVAILABLE, str(error)) from error
    base = views_directory(state_root)
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        root = _claim_root(base, origin)
    except OSError as error:
        raise GateViewError(GATE_VIEW_UNAVAILABLE, f"no directory for the gate view under {base}: {error}") from error
    view = GateView(root=root, origin=os.path.realpath(origin), workspace_path=origin, environment_roots=environment_roots)
    try:
        for relpath in sorted(paths):
            if relpath.startswith(KRIYA_DIRECTORY_NAME + "/") or relpath in _NEVER_COPIED or relpath.startswith(".git/") or os.path.isabs(relpath) \
                    or ".." in relpath.split("/"):
                continue
            source = os.path.join(origin, relpath)
            if manifest is None or relpath not in expected:
                expected[relpath] = _content_identity(source)
            _copy_entry(source, os.path.join(root, relpath))
        mismatched = sorted(path for path, identity in expected.items()
                            if path in paths and _content_identity(os.path.join(root, path)) != identity)
        if mismatched:
            raise GateViewError(GATE_VIEW_IDENTITY_MISMATCH,
                                f"the view does not hold the bound content of {', '.join(mismatched[:10])}")
        view.expected = {path: identity for path, identity in expected.items() if path in paths}
        view.stats = {path: _stat_identity(os.path.join(root, path)) for path in view.expected}
    except GateViewError:
        view.close()
        raise
    except (OSError, shutil.Error) as error:
        view.close()
        raise GateViewError(GATE_VIEW_UNAVAILABLE, f"the view of the bound tree could not be built: {error}") from error
    return view


def _copy_entry(source: str, destination: str) -> None:
    """One entry, as itself: a link stays a link (never followed), a file keeps its mode and times, an absent path stays absent, a
    directory entry (a Git submodule) becomes an empty directory. Anything else is refused."""
    if not os.path.lexists(source):
        return
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    mode = os.lstat(source).st_mode
    if stat.S_ISLNK(mode):
        os.symlink(os.readlink(source), destination)
    elif stat.S_ISREG(mode):
        shutil.copy2(source, destination, follow_symlinks=False)
    elif stat.S_ISDIR(mode):
        os.makedirs(destination, exist_ok=True)
    else:
        raise GateViewError(GATE_VIEW_UNAVAILABLE, f"{source} is neither a file, a link nor a directory")


def guard_against_origin(view: GateView, argv: Iterable[str], cwd: str) -> None:
    """No silent path back to the shared worktree: a command run for a view whose working directory or an argument names the origin
    tree would execute the worktree's bytes while the verdict is attributed to the view. Refused."""
    offenders: Tuple[str, ...] = tuple(token for token in (cwd, *argv) if view.refers_to_origin(token))
    if offenders:
        raise GateViewError(GATE_VIEW_PATH_CONFUSION,
                            f"a command for the gate view names the shared worktree ({', '.join(offenders[:3])}); "
                            "it would run on bytes the view does not hold")

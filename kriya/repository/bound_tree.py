"""REPOSITORY: the ONE materialisation of the canonical bound tree (Wave-2 Step 2, R5/R6).

The bound tree is Git's own view of a work tree (``kriya.repository.snapshot.visible_repository_paths``: every tracked path and every untracked non-ignored one, ``.git``
and ``.kriya`` never). ``copy_bound_tree`` copies exactly that set, byte for byte and mode for mode (a link stays a link, a Git submodule's directory entry becomes an
empty directory), and proves every copy against the identity it was read with. A gate view (``kriya/tools/gate_view.py``) and every authority or baseline copy of the
project go through this module, so no site can decide membership by a directory name: a legitimate ``build/Util.java`` or ``com/acme/target/T.java`` is in, a
git-ignored ``target/classes`` is out. A tree Git cannot list is a typed ``BoundTreeError`` - there is no name-based approximation to fall back to.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from typing import Dict, Optional, Set, Tuple

from kriya.config.environment import KRIYA_DIRECTORY_NAME
from kriya.platform.filesystem_semantics import identity_key
from kriya.repository.git import RepositoryContentFilterRefused, is_plainly_not_a_repository, work_tree_root
from kriya.repository.snapshot import _content_identity, repository_paths_by_declaration, visible_repository_paths

BOUND_TREE_UNAVAILABLE = "BOUND_TREE_UNAVAILABLE"
BOUND_TREE_IDENTITY_MISMATCH = "BOUND_TREE_IDENTITY_MISMATCH"

_NEVER_COPIED = (".git", KRIYA_DIRECTORY_NAME)


class BoundTreeError(Exception):
    """The bound tree could not be materialised or proved. ``reason_code`` is BOUND_TREE_UNAVAILABLE or BOUND_TREE_IDENTITY_MISMATCH; ``str(error)`` is
    ``"<REASON_CODE>: <detail>"``."""

    def __init__(self, reason_code: str, detail: str, paths: Tuple[str, ...] = ()) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail
        self.paths = paths          # for an identity mismatch: the paths whose copy differs from the bound content


def copy_entry(source: str, destination: str) -> None:
    """One entry, as itself: a link stays a link (never followed), a file keeps its mode and times, an absent path stays absent, a directory entry (a Git
    submodule) becomes an empty directory. Anything else is refused."""
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
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{source} is neither a file, a link nor a directory")


def copy_and_prove(origin: str, root: str, paths: Set[str], expected: Dict[str, Optional[str]], *, manifest_given: bool) -> None:
    """Copy every bound path of ``origin`` into ``root`` and prove each copy against the identity it was bound to (``expected`` is filled for the paths that
    carried none). The one copy-and-prove loop."""
    for relpath in sorted(paths):
        if relpath.startswith(KRIYA_DIRECTORY_NAME + "/") or relpath in _NEVER_COPIED or relpath.startswith(".git/") or os.path.isabs(relpath) \
                or ".." in relpath.split("/"):
            continue
        source = os.path.join(origin, relpath)
        if not manifest_given or relpath not in expected:
            expected[relpath] = _content_identity(source)
        copy_entry(source, os.path.join(root, relpath))
    mismatched = sorted(path for path, identity in expected.items()
                        if path in paths and _content_identity(os.path.join(root, path)) != identity)
    if mismatched:
        raise BoundTreeError(BOUND_TREE_IDENTITY_MISMATCH, f"the copy does not hold the bound content of {', '.join(mismatched[:10])}", tuple(mismatched))


EXACT_GIT_ROOT = "EXACT_GIT_ROOT"                        # root IS the top level of its own Git work tree: the bound tree is its membership authority
NESTED_IN_GIT_WORK_TREE = "NESTED_IN_GIT_WORK_TREE"      # root sits inside ANOTHER repository's work tree (no .git of its own): no membership owner (PARKED)
NO_GIT_REPOSITORY = "NO_GIT_REPOSITORY"                  # git plainly answers "not a git repository" (a pre-bootstrap greenfield folder): no membership owner (PARKED)


def _root_class_and_top(root: str) -> Tuple[str, Optional[str]]:
    """``(workspace_root_class, Git top level or None)`` from ONE resolution - the body of ``workspace_root_class``."""
    try:
        top = work_tree_root(root)
        if top is None:
            if is_plainly_not_a_repository(root):
                return NO_GIT_REPOSITORY, None
            raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{root} is not the root of a Git work tree: Git refuses it or it has no work tree")
    except (RepositoryContentFilterRefused, subprocess.SubprocessError, OSError) as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the Git work tree of {root} could not be resolved: {error}") from error
    root_identity = identity_key(root)
    if root_identity is None or identity_key(top) is None:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{root} is not the root of its Git work tree: the identity of {root} or of the Git top level {top} is unresolvable")
    return (EXACT_GIT_ROOT if root_identity == identity_key(top) else NESTED_IN_GIT_WORK_TREE), top


def workspace_root_class(root: str) -> str:
    """W3-3 A2: which membership authority exists for ``root`` - the ONE classification every root-class dispatch consumes (``work_tree_root`` and
    ``identity_key``; no second root discovery). ``EXACT_GIT_ROOT``: the bound tree is the authority. ``NESTED_IN_GIT_WORK_TREE`` and ``NO_GIT_REPOSITORY``:
    no canonical membership owner exists today (NESTED-RAW-WORKSPACE-BOUND-TREE-AUTHORITY-OWNER-001; the pre-bootstrap design stage, A0.2), a caller that keeps
    a legacy behaviour for them says so by name. Anything git cannot ANSWER - an OSError, a timeout, an unparseable answer, a repository git refuses to use, a
    bare repository, an unresolvable identity - is the typed ``BoundTreeError(BOUND_TREE_UNAVAILABLE)``: a refusal is never an absence, so it can never fall
    through to the no-git behaviour."""
    return _root_class_and_top(root)[0]


def _require_exact_work_tree_root(root: str) -> None:
    """W3-3 A2-0: Git's membership is the bound tree of ``root`` only when ``root`` IS the top level of its work tree. A folder merely nested in another
    repository is not: ``git ls-files`` run from it answers with the ENCLOSING repository's ignore rules and tracked set (MEASURED at 2880cba: an outer
    ``.gitignore`` hid a legitimate ``inner/build/Util.java``), so that policy would silently become the folder's authority. Compared by filesystem identity
    (``identity_key``: every alias of one directory is one key), the same way the project recognises one directory under several spellings."""
    root_class, top = _root_class_and_top(root)
    if root_class == NO_GIT_REPOSITORY:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{root} is not the root of a Git work tree")
    if root_class == NESTED_IN_GIT_WORK_TREE:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{root} is not the root of its Git work tree: Git resolves the enclosing repository {top}")


def bound_tree_paths(root: str) -> frozenset:
    """The membership of the canonical bound tree of ``root``: every path Git tracks or leaves visible (``visible_repository_paths``), never a directory
    name. ``root`` must be the exact top level of its Git work tree (a folder nested in another repository has no bound tree of its own). A root Git
    cannot list (not a work tree, an enclosing repository's folder, a failing listing) is the typed ``BoundTreeError(BOUND_TREE_UNAVAILABLE)``: there is no
    name-based approximation to fall back to, and a caller must never read that error as "no such file"."""
    _require_exact_work_tree_root(root)
    try:
        return visible_repository_paths(root)
    except RuntimeError as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, str(error)) from error


def bound_tree_declaration(root: str) -> Tuple[frozenset, frozenset]:
    """W3-3 A2 follow-on: ``(tracked, untracked)`` of the canonical bound tree of ``root`` - the same membership as ``bound_tree_paths`` (their union), the same
    exact-root proof, the same typed ``BoundTreeError``, from ONE Git listing (``snapshot.repository_paths_by_declaration``). Membership answers "which files exist
    for consideration"; this split is only Git's own declaration (tracked vs merely present) for a caller that must tell positive declaration from presence."""
    _require_exact_work_tree_root(root)
    try:
        return repository_paths_by_declaration(root)
    except RuntimeError as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, str(error)) from error


def copy_bound_tree(origin: str, destination: str) -> frozenset:
    """Copy the canonical bound tree of ``origin`` into ``destination`` (created if absent) and prove it. Membership is the repository's, never a directory name.
    An origin Git cannot list (not a work tree) or a destination that cannot be written is a typed ``BoundTreeError``.
    Returns the relative paths now present in ``destination`` (a tracked path deleted in ``origin`` is not among them)."""
    paths = set(bound_tree_paths(origin))
    try:
        os.makedirs(destination, exist_ok=True)
        copy_and_prove(origin, destination, paths, {}, manifest_given=False)
    except BoundTreeError:
        raise
    except (OSError, shutil.Error) as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the bound tree could not be copied: {error}") from error
    return frozenset(path for path in paths if os.path.lexists(os.path.join(destination, path)))

"""REPOSITORY: the ONE materialisation of the canonical bound tree (Wave-2 Step 2, R5/R6).

The bound tree is Git's own view of a work tree (``kriya.repository.snapshot.visible_repository_paths``: every tracked path and every untracked non-ignored one, ``.git``
and ``.kriya`` never). ``copy_bound_tree`` copies exactly that set, byte for byte and mode for mode (a link stays a link, a Git submodule's directory entry becomes an
empty directory), and proves every copy against the identity it was read with. A gate view (``kriya/tools/gate_view.py``) and every authority or baseline copy of the
project go through this module, so no site can decide membership by a directory name: a legitimate ``build/Util.java`` or ``com/acme/target/T.java`` is in, a
git-ignored ``target/classes`` is out. A tree Git cannot list is a typed ``BoundTreeError`` - there is no name-based approximation to fall back to.

The membership root is the OPERATING root (the directory a run was invoked in), listed with the ignore rules and tracked set of the repository its D-001 authority
root names: ``<repo>/sub`` gets exactly the files under ``sub`` (relative to it, no sibling), a nested repository with its own ``.git`` gets its own tree.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from typing import Dict, Optional, Set, Tuple

from kriya.config.environment import KRIYA_DIRECTORY_NAME
from kriya.context.workspace_identity import authority_root
from kriya.platform.filesystem_semantics import identity_key
from kriya.repository.git import RepositoryContentFilterRefused, is_plainly_not_a_repository, work_tree_root
from kriya.repository.snapshot import (
    _content_identity,
    ignored_repository_paths,
    repository_paths_by_declaration,
    visible_repository_paths,
)

BOUND_TREE_UNAVAILABLE = "BOUND_TREE_UNAVAILABLE"
BOUND_TREE_IDENTITY_MISMATCH = "BOUND_TREE_IDENTITY_MISMATCH"
BOUND_TREE_OPERATING_SCOPE_EXCLUDED = "BOUND_TREE_OPERATING_SCOPE_EXCLUDED"      # W3-3 U3: the invoked operating root is itself excluded by the authority repository's ignore rules
BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY = "BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY"    # W3-3 U4: a non-Git workspace holding user entries has no brownfield source authority
KRIYA_CONFIG_FILE_NAME = "kriya.yaml"       # W3-3 U4: the one canonical configuration name that is control-plane state (not a user entry) in an otherwise empty NO_GIT workspace

_NEVER_COPIED = (".git", KRIYA_DIRECTORY_NAME)


class BoundTreeError(Exception):
    """The bound tree could not be materialised or proved. ``reason_code`` is BOUND_TREE_UNAVAILABLE, BOUND_TREE_IDENTITY_MISMATCH,
    BOUND_TREE_OPERATING_SCOPE_EXCLUDED or BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY; ``str(error)`` is ``"<REASON_CODE>: <detail>"``."""

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


GIT_BACKED = "GIT_BACKED"                    # the D-001 authority root of the operating root is the top level of a Git work tree: Git's membership applies
NO_GIT_REPOSITORY = "NO_GIT_REPOSITORY"      # no repository at all (a pre-bootstrap greenfield folder): no membership owner (PARKED)


def _root_class(root: str) -> str:
    """W3-3 (D-001): which membership authority exists for the OPERATING root ``root`` (the directory the run was invoked in and operates on).

    The repository the operating root belongs to is the one the D-001 workspace identity names - ``workspace_identity.authority_root``, the nearest ``.git`` entry
    at or above it (the one repository search; none is added here). So ``<repo>``, ``<repo>/sub``, a new empty ``<repo>/sub/dir`` and a ``.git``-less raw child
    all belong to ``<repo>`` (D-001 pins this); a nested repository with its own ``.git`` and a linked work tree are their own authority. The proof is that Git
    agrees: ``identity_key(git_toplevel) == identity_key(authority_root)`` - NOT that the operating root equals Git's toplevel. Anything Git cannot ANSWER, or
    answers differently from D-001 (an OSError, a timeout, an unparseable answer, a repository git refuses, a bare repository, Git resolving a repository the
    D-001 search did not find or another one than it found, an unresolvable identity), is the typed ``BoundTreeError(BOUND_TREE_UNAVAILABLE)``: a refusal is never
    an absence, so it can never fall through to the no-git behaviour."""
    try:
        authority = authority_root(root)
        top = work_tree_root(root)
        repository_found = os.path.lexists(os.path.join(authority, ".git"))
        if top is None:
            if repository_found:
                raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"Git cannot resolve the work tree of {root}, which belongs to the repository at {authority}")
            if is_plainly_not_a_repository(root):
                return NO_GIT_REPOSITORY
            raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{root} is not the root of a Git work tree: Git refuses it or it has no work tree")
    except (RepositoryContentFilterRefused, subprocess.SubprocessError, OSError) as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the Git work tree of {root} could not be resolved: {error}") from error
    if not repository_found:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"Git resolves the work tree {top} for {root}, but the workspace authority search found no repository at or above it")
    authority_identity, top_identity = identity_key(authority), identity_key(top)
    if authority_identity is None or top_identity is None:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the identity of {root}'s authority root {authority} or of the Git top level {top} is unresolvable")
    if authority_identity != top_identity:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the workspace authority root of {root} is {authority}, but Git resolves the work tree {top}")
    return GIT_BACKED


def workspace_root_class(root: str) -> str:
    """``GIT_BACKED`` (the bound tree is the authority) or ``NO_GIT_REPOSITORY`` (no canonical membership owner today: the pre-bootstrap design stage, A0.2); anything
    else is the typed ``BoundTreeError`` (see ``_root_class``)."""
    return _root_class(root)


def _is_canonical_kriya_config(root: str, name: str) -> bool:
    """Kriya's own canonical configuration: exactly the entry ``kriya.yaml`` directly under ``root`` and a regular file (a directory, a symlink or any other name is a user entry)."""
    if name != KRIYA_CONFIG_FILE_NAME:
        return False
    try:
        return stat.S_ISREG(os.lstat(os.path.join(root, name)).st_mode)
    except OSError:
        return False


def admit_workspace_for_mutation(root: str) -> None:
    """W3-3 U4 (owner decisions 2026-10-31): the production admission of a workspace that has no Git repository. An EMPTY one - no entry except Kriya's own control-plane
    artifacts, the ``.kriya`` state directory and the canonical root ``kriya.yaml`` (a regular file: Kriya's configuration is control-plane state, not a source baseline) - is a
    greenfield: the existing Git bootstrap makes it a repository before any model-originated write. Any other entry (a README, a ``.gitignore``, source, a user's hidden
    file, a ``kriya.yml``, a ``kriya.yaml`` that is a directory or a symlink) makes it a brownfield WITHOUT source authority - the filesystem is never a membership authority and the bootstrap never commits user files - so it is the typed
    ``BoundTreeError(BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY)``, raised before the contract is compiled and before anything is generated. A Git-backed root is always
    admitted here (its membership is Git's); a directory that does not exist is left to the caller; one that cannot be listed is ``BOUND_TREE_UNAVAILABLE``."""
    if not os.path.isdir(root) or _root_class(root) != NO_GIT_REPOSITORY:
        return
    try:
        entries = sorted(name for name in os.listdir(root) if name != KRIYA_DIRECTORY_NAME and not _is_canonical_kriya_config(root, name))
    except OSError as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the entries of {root} could not be listed: {error}") from error
    if entries:
        raise BoundTreeError(
            BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY,
            f"{root} is not a Git repository and already holds {', '.join(entries[:5])}{' ...' if len(entries) > 5 else ''}: Kriya will not derive source membership from the filesystem or "
            "commit existing files for you. Run `git init` and commit a baseline in it (or run Kriya from inside an existing repository), or start from an empty folder.",
            tuple(entries[:10]),
        )


def _require_git_backed(root: str) -> None:
    """The membership of ``root`` is a bound tree only when its D-001 authority root is the top level of a Git work tree that Git agrees with (``_root_class``)."""
    if _root_class(root) == NO_GIT_REPOSITORY:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"{root} is not the root of a Git work tree")


def bound_tree_paths(root: str) -> frozenset:
    """The membership of the canonical bound tree of the operating root ``root``: every path Git tracks or leaves visible (``visible_repository_paths``) under it,
    relative to it, never a directory name. ``root`` must belong to a Git-backed D-001 authority root that Git agrees with (``_root_class``); a root Git cannot list
    (no repository, a repository Git refuses or disagrees with D-001 about, a failing listing) is the typed ``BoundTreeError(BOUND_TREE_UNAVAILABLE)``: there is no
    name-based approximation to fall back to, and a caller must never read that error as "no such file"."""
    _require_git_backed(root)
    try:
        return visible_repository_paths(root)
    except RuntimeError as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, str(error)) from error


def bound_tree_declaration(root: str) -> Tuple[frozenset, frozenset]:
    """W3-3 A2 follow-on: ``(tracked, untracked)`` of the canonical bound tree of ``root`` - the same membership as ``bound_tree_paths`` (their union), the same
    D-001 authority-root proof, the same typed ``BoundTreeError``, from ONE Git listing (``snapshot.repository_paths_by_declaration``). Membership answers "which files exist
    for consideration"; this split is only Git's own declaration (tracked vs merely present) for a caller that must tell positive declaration from presence."""
    _require_git_backed(root)
    try:
        return repository_paths_by_declaration(root)
    except RuntimeError as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, str(error)) from error


def copy_bound_paths(origin: str, destination: str, paths: Set[str]) -> frozenset:
    """Copy exactly ``paths`` - a bound-tree membership the caller already holds (``bound_tree_paths`` / ``bound_tree_declaration``) - from ``origin`` into
    ``destination`` (created if absent) and prove each copy. The one copy-and-prove entry point: ``copy_bound_tree`` lists and calls it; a caller that must act on the
    SAME membership it copies (the scoped sandbox's alias refusal) lists once and calls it, so no second listing can disagree with the copy. A destination that cannot
    be written is a typed ``BoundTreeError``. Returns the relative paths now present in ``destination``."""
    try:
        os.makedirs(destination, exist_ok=True)
        copy_and_prove(origin, destination, set(paths), {}, manifest_given=False)
    except BoundTreeError:
        raise
    except (OSError, shutil.Error) as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"the bound tree could not be copied: {error}") from error
    return frozenset(path for path in paths if os.path.lexists(os.path.join(destination, path)))


def copy_bound_tree(origin: str, destination: str) -> frozenset:
    """Copy the canonical bound tree of ``origin`` into ``destination`` (created if absent) and prove it. Membership is the repository's, never a directory name.
    An origin Git cannot list (not a work tree) or a destination that cannot be written is a typed ``BoundTreeError``.
    Returns the relative paths now present in ``destination`` (a tracked path deleted in ``origin`` is not among them)."""
    return copy_bound_paths(origin, destination, set(bound_tree_paths(origin)))


def operating_root_excluded_by_authority(root: str) -> bool:
    """W3-3 U3: whether Git's own ignore rules of the D-001 authority repository exclude the OPERATING root ``root`` itself (the root directly, or through an ignored
    ancestor). The question is asked FROM the authority root about the root's path relative to it, without a trailing slash: asked from inside the root, its own
    ``.gitignore`` (``*``) would answer for the root itself (MEASURED). The authority root itself is never excluded. It is read only when the bound listing is empty
    (an empty listing alone cannot tell a new empty subtree from an ignored one); a Git that cannot answer is the typed ``BoundTreeError(BOUND_TREE_UNAVAILABLE)``."""
    _require_git_backed(root)
    try:
        authority = os.path.realpath(authority_root(root))
        relative = os.path.relpath(os.path.realpath(root), authority)
        if relative == os.curdir:
            return False
        return bool(ignored_repository_paths(authority, [relative.replace(os.sep, "/")]))
    except (RuntimeError, OSError, ValueError) as error:
        raise BoundTreeError(BOUND_TREE_UNAVAILABLE, f"Git could not say whether {root} is excluded by its repository: {error}") from error

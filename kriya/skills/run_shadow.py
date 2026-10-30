"""The run-local shadow of the skills directories that live inside a bound candidate repository (GIT-MIDRUN-COMMITS-S3-001).

An enforce run binds its candidate identity (the plan sandbox against the workspace's HEAD and content) and then keeps
learning: skeletons, gap acknowledgements, extracted rules, conflict staging and resolutions, rule provenance, staged
lessons. Every one of those writers writes into the skills directory and (but for the staging ones) commits it, so while
the skills directory is inside the workspace repository each write moved the repository under the gates that still had to
verify the candidate (`GIT_METADATA_STALE` at the terminal gates, or a candidate whose content changed after it was bound).

The shadow is the one owner of the correction. For the run it holds a COPY of each such skills directory under Kriya's
state directory; the owning `SkillEngine` points every loaded skill (`source_path`) and its effective skills root at the
copy, so every same-run writer and reader sees the same view and the candidate repository is not touched. It is not a second
authority: it is an uncommitted transaction owned by one engine, in memory and on disk under the state directory only.

  * `flush` runs once, after the candidate's terminal verification succeeded and its commit completed. It refuses when the
    canonical skills content no longer equals the snapshot the shadow was taken from (a typed conflict - never last writer
    wins), otherwise applies exactly the shadow's delta, commits exactly those paths, and on ANY failure before the commit is
    durable restores the canonical paths (files, created directories and the index entries this flush staged) to their
    pre-flush state, so a failed flush leaves no dirty learning residue and never claims durability it did not get.
  * `discard` drops the copy (the terminal verification failed): the canonical skills are not touched at all.

Scope is deliberately small: no generic transaction framework, only the skills roots a run activated and the exact paths the
flush wrote.
"""
import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from kriya.config.environment import child_environment
from kriya.platform import git_argv, git_environment
from kriya.platform.filesystem_semantics import PathRelation, path_relation

logger = logging.getLogger(__name__)

REASON_CANONICAL_CHANGED = "SKILLS_CANONICAL_CHANGED"
REASON_FLUSH_FAILED = "SKILLS_FLUSH_FAILED"
REASON_ROOT_IN_WORK_TREE = "SKILL_SHADOW_ROOT_IN_WORK_TREE"
REASON_ROOT_ENCLOSES_REPOSITORY = "SKILL_ROOT_ENCLOSES_REPOSITORY"
REASON_ROOT_RELATION_UNKNOWN = "SKILL_ROOT_RELATION_UNKNOWN"

_IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"})
_LINK = "symlink:"


class RunShadowRefused(RuntimeError):
    """The shadow cannot be made safely; the run must not start with a skills directory that would drift the candidate."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_ACTIVE_BASES = set()


def is_shadow_path(path: str) -> bool:
    """True when ``path`` is inside a live run-local shadow. Nothing there is ever committed: it is a copy, not repository content,
    whatever git work tree (the user's dotfiles repository, say) happens to hold the state directory."""
    return any(path_relation(base, path) is PathRelation.WITHIN for base in _ACTIVE_BASES)


def _remove(base: str) -> None:
    """Removes one workspace's shadow and, when that leaves it empty, the `run_shadow` directory holding it."""
    _ACTIVE_BASES.discard(base)
    shutil.rmtree(base, ignore_errors=True)
    try:
        os.rmdir(os.path.dirname(base))
    except OSError:
        pass


def _skip(name: str) -> bool:
    return name in _IGNORED_NAMES or name.endswith(".pyc")


def manifest(root: str) -> Dict[str, str]:
    """``{posix relative path: sha256 of the bytes | 'symlink:<target>'}`` of every file under ``root`` (empty when absent)."""
    entries: Dict[str, str] = {}
    if not os.path.isdir(root):
        return entries
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip(d))
        for name in sorted(filenames) + [d for d in dirnames if os.path.islink(os.path.join(current, d))]:
            if _skip(name):
                continue
            path = os.path.join(current, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if os.path.islink(path):
                entries[relative] = _LINK + os.readlink(path)
            else:
                with open(path, "rb") as handle:
                    entries[relative] = hashlib.sha256(handle.read()).hexdigest()
    return entries


def _git(directory: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(git_argv(["git", *args]), cwd=directory, capture_output=True, text=True, env=git_environment(child_environment()))


def _existing_ancestor(path: str) -> str:
    current = os.path.abspath(path)
    while not os.path.isdir(current) and os.path.dirname(current) != current:
        current = os.path.dirname(current)
    return current


def _toplevel(path: str) -> Optional[str]:
    """The git work tree holding ``path`` (its nearest existing ancestor), or None."""
    result = _git(_existing_ancestor(path), "rev-parse", "--show-toplevel")
    return os.path.realpath(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None


def inside_bound_repository(directory: str, workspace_path: str) -> bool:
    """True when ``directory`` is part of the repository the run binds: beneath the workspace, or in the same git work tree
    as the workspace (a workspace that is a sub-directory of a larger repository still commits into that repository)."""
    relation = path_relation(workspace_path, directory)
    if relation is PathRelation.UNKNOWN:
        raise RunShadowRefused(REASON_ROOT_RELATION_UNKNOWN, f"cannot tell whether the skills root {directory!r} is inside the workspace")
    if relation is PathRelation.WITHIN:
        return True
    workspace_tree, skills_tree = _toplevel(workspace_path), _toplevel(directory)
    return workspace_tree is not None and workspace_tree == skills_tree


def _rebase(path: str, old_root: str, new_root: str) -> str:
    relative = os.path.relpath(os.path.realpath(path), os.path.realpath(old_root))
    return new_root if relative == "." else os.path.join(new_root, relative)


@dataclass
class ShadowRoot:
    canonical: str
    shadow: str
    snapshot: Dict[str, str]


@dataclass
class RunShadow:
    roots: List[ShadowRoot] = field(default_factory=list)
    base: str = ""

    # -- creation -----------------------------------------------------------------------------------------------------
    @classmethod
    def create(cls, skills_dirs: List[str], workspace_path: str, state_directory: str) -> Optional["RunShadow"]:
        """The shadow for the ``skills_dirs`` that are inside the bound repository, or None when none is (nothing to
        protect: the existing behaviour is kept). Raises `RunShadowRefused` when a protected directory cannot be shadowed."""
        workspace = os.path.realpath(workspace_path)
        inside = [d for d in skills_dirs if inside_bound_repository(d, workspace)]
        if not inside:
            return None
        for directory in inside:
            real = os.path.realpath(directory)
            if real == workspace or path_relation(real, workspace) is PathRelation.WITHIN or os.path.lexists(os.path.join(real, ".git")):
                raise RunShadowRefused(REASON_ROOT_ENCLOSES_REPOSITORY, f"the skills root {directory!r} is the repository itself (or holds it); it cannot be shadowed")
        # One per workspace: the workspace lock admits one run at a time, so whatever an earlier run left is stale.
        base = os.path.join(os.path.realpath(state_directory), "run_shadow", hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:16])
        _remove(base)
        os.makedirs(base)
        _ACTIVE_BASES.add(base)
        shadow = cls(base=base)
        try:
            base_tree = _toplevel(base)
            if path_relation(workspace, base) is not PathRelation.OUTSIDE or (base_tree is not None and base_tree == _toplevel(workspace)):
                raise RunShadowRefused(REASON_ROOT_IN_WORK_TREE, f"the shadow location {base!r} is inside the repository the run binds; its writes would move it")
            for index, directory in enumerate(inside):
                canonical = os.path.abspath(directory)
                target = os.path.join(base, str(index))
                snapshot = manifest(canonical)
                if os.path.isdir(canonical):
                    shutil.copytree(canonical, target, symlinks=True, ignore=shutil.ignore_patterns(*_IGNORED_NAMES, "*.pyc"))
                else:
                    os.makedirs(target)
                shadow.roots.append(ShadowRoot(canonical, target, snapshot))
        except BaseException:
            _remove(base)
            raise
        return shadow

    # -- the effective view -------------------------------------------------------------------------------------------
    def effective(self, path: str) -> str:
        """``path`` (a canonical skills directory, or a path beneath one) as the run sees it."""
        for root in self.roots:
            if path_relation(root.canonical, path) is PathRelation.WITHIN:
                return _rebase(path, root.canonical, root.shadow)
        return path

    def canonical(self, path: str) -> str:
        """The inverse of `effective`."""
        for root in self.roots:
            if path_relation(root.shadow, path) is PathRelation.WITHIN:
                return _rebase(path, root.shadow, root.canonical)
        return path

    # -- the end of the run -------------------------------------------------------------------------------------------
    def discard(self) -> None:
        _remove(self.base)

    def flush(self, message: str, commit: Callable[[str, List[str], str], bool]) -> List[Dict[str, object]]:
        """Persists the shadow's delta into the canonical skills; returns one failure record per root that could not be
        persisted (empty = everything durable). Never raises. The shadow is removed afterwards in every case."""
        failures: List[Dict[str, object]] = []
        try:
            for root in self.roots:
                failure = self._flush_root(root, message, commit)
                if failure is not None:
                    failures.append(failure)
        except Exception as error:  # noqa: BLE001 - the candidate is already committed; a bookkeeping failure is reported, never raised
            failures.append({"operation": "skill_learning_flush", "reason_code": REASON_FLUSH_FAILED,
                             "error": f"{type(error).__name__}: {error}"})
        finally:
            self.discard()
        return failures

    def _flush_root(self, root: ShadowRoot, message: str, commit: Callable[[str, List[str], str], bool]) -> Optional[Dict[str, object]]:
        current = manifest(root.canonical)
        if current != root.snapshot:
            changed = sorted(p for p in set(current) | set(root.snapshot) if current.get(p) != root.snapshot.get(p))
            return {"operation": "skill_learning_flush", "reason_code": REASON_CANONICAL_CHANGED, "root": root.canonical, "paths": changed,
                    "error": "the canonical skills changed while the run was in progress; the run's learning was not persisted "
                             "and nothing was overwritten"}
        shadow_now = manifest(root.shadow)
        changed_paths = sorted(p for p in set(shadow_now) | set(root.snapshot) if shadow_now.get(p) != root.snapshot.get(p))
        if not changed_paths:
            return None
        journal = _Journal(root.canonical)
        try:
            for relative in changed_paths:
                journal.apply(relative, os.path.join(root.shadow, relative) if relative in shadow_now else None)
        except Exception as error:  # noqa: BLE001 - restored below and reported
            return self._failed(root, journal, changed_paths, f"writing the run's learning failed: {type(error).__name__}: {error}")
        directory = _existing_ancestor(root.canonical)
        # A path the repository ignores is not repository content (`git add` refuses it): it is persisted as a file and is not committed.
        committable = _not_ignored(directory, [os.path.join(root.canonical, relative) for relative in changed_paths])
        if not committable or commit(directory, committable, message):
            return None
        return self._failed(root, journal, changed_paths, "the run's learning was written but its Git commit failed")

    def _failed(self, root: ShadowRoot, journal: "_Journal", changed_paths: List[str], reason: str) -> Dict[str, object]:
        problems = journal.restore()
        problems += _unstage(_existing_ancestor(root.canonical), [os.path.join(root.canonical, p) for p in changed_paths])
        if manifest(root.canonical) != root.snapshot:
            problems.append("the canonical skills do not equal their pre-flush state")
        record: Dict[str, object] = {
            "operation": "skill_learning_flush", "reason_code": REASON_FLUSH_FAILED, "root": root.canonical, "paths": changed_paths,
            "error": f"{reason}; the canonical skills were restored to their pre-flush state and the run's learning was not persisted"
                     if not problems else f"{reason}; restoring the canonical skills FAILED ({'; '.join(problems)}); inspect {root.canonical}",
        }
        return record


def _not_ignored(directory: str, paths: List[str]) -> List[str]:
    if _toplevel(directory) is None:
        return paths
    result = _git(directory, "check-ignore", "--", *paths)          # exit 0: some are ignored (listed), 1: none, 128: error
    if result.returncode not in (0, 1):
        return paths
    ignored = {line for line in result.stdout.splitlines() if line}
    return [p for p in paths if p not in ignored]


def _unstage(directory: str, paths: List[str]) -> List[str]:
    """Drops the index entries the flush staged for exactly ``paths`` (the index only: no tree, no HEAD, no ref). Outside a
    work tree there is no index to restore."""
    if _toplevel(directory) is None:
        return []
    result = _git(directory, "reset", "-q", "--", *paths)
    return [] if result.returncode == 0 else [f"git reset of the index failed: {result.stderr.strip()}"]


class _Journal:
    """The pre-flush state of every canonical path the flush touched, so a failed flush can put each back."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.entries: List[Tuple[str, Optional[Tuple[str, object]]]] = []      # (path, preimage); preimage None = did not exist
        self.created_dirs: List[str] = []

    def apply(self, relative: str, source: Optional[str]) -> None:
        target = os.path.join(self.root, relative)
        self.entries.append((target, self._preimage(target)))
        if source is None:
            os.remove(target)
            return
        parent = os.path.dirname(target)
        missing = []
        while not os.path.isdir(parent):
            missing.append(parent)
            parent = os.path.dirname(parent)
        for directory in reversed(missing):
            os.mkdir(directory)
            self.created_dirs.append(directory)
        if os.path.lexists(target):
            os.remove(target)
        if os.path.islink(source):
            os.symlink(os.readlink(source), target)
        else:
            shutil.copy2(source, target)

    @staticmethod
    def _preimage(target: str) -> Optional[Tuple[str, object]]:
        if os.path.islink(target):
            return ("link", os.readlink(target))
        if os.path.isfile(target):
            with open(target, "rb") as handle:
                return ("file", (handle.read(), os.stat(target).st_mode))
        return None

    def restore(self) -> List[str]:
        problems: List[str] = []
        for target, preimage in reversed(self.entries):
            try:
                if os.path.lexists(target):
                    os.remove(target)
                if preimage is None:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                kind, value = preimage
                if kind == "link":
                    os.symlink(str(value), target)
                else:
                    data, mode = value  # type: ignore[misc]
                    with open(target, "wb") as handle:
                        handle.write(data)
                    os.chmod(target, mode)
            except OSError as error:
                problems.append(f"{target}: {error}")
        for directory in reversed(self.created_dirs):
            try:
                os.rmdir(directory)
            except OSError as error:
                problems.append(f"{directory}: {error}")
        return problems

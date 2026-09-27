"""Scan scope, change set and PRE/POST snapshots (PRD-031A §4.3, §5.4).

The committed batch is the change set, not the scan scope. The scope is the
provider's minimum trustworthy scope (or a broader configured one), resolved
here from the real workspace, which is the pristine base until commit.

Both snapshots are built in the same pass that hashes the scope, so each
file is read once and what is scanned is exactly what is hashed:

* unchanged file: PRE and POST, byte-identical;
* modified file: PRE base bytes, POST candidate bytes;
* added file: POST only;
* deleted file: PRE only.

Kriya enforces the size limit and the operator's exclusions before anything
reaches a provider; scanner-control files never enter a snapshot.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from kriya.static_analysis.coverage import language_of
from kriya.static_analysis.model import (
    BASELINE_IDENTITY_MISMATCH,
    SCOPE_UNRESOLVABLE,
    ChangedPath,
    ChangeKind,
    OversizedTarget,
    ScanScope,
    ScopePlan,
    Side,
    bytes_digest,
)
from kriya.workflow.edit_safety import content_revision

# Build descriptors marking a module root (provider-neutral; the markers
# kriya/tools/validate.py's stack detection uses, plus common others).
BUILD_DESCRIPTORS = frozenset({
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt",
    "Gemfile", "Rakefile", "go.mod", "Cargo.toml", "composer.json", "CMakeLists.txt",
})
_SKIP_DIRS = frozenset({".git", ".kriya", ".hg", ".svn"})


class ScopeError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _safe_relpath(relpath: str) -> str:
    normalized = os.path.normpath(relpath).replace(os.sep, "/")
    if os.path.isabs(relpath) or normalized == ".." or normalized.startswith("../"):
        raise ScopeError(SCOPE_UNRESOLVABLE, f"path {relpath!r} escapes the workspace")
    return normalized


def workspace_relpath(target_path: str, workspace_root: str) -> str:
    """``target_path`` relative to the workspace, comparing real paths: a
    workspace reached through a symlink (macOS /var -> /private/var) gives
    the same relative path as its real location."""
    real_target = os.path.join(
        os.path.realpath(os.path.dirname(os.path.abspath(target_path))), os.path.basename(target_path),
    )
    return _safe_relpath(os.path.relpath(real_target, os.path.realpath(workspace_root)))


def _read_regular(path: str) -> Optional[bytes]:
    """Bytes of a regular, non-symlink file; None when absent."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


def _write_bytes(root: str, relpath: str, data: bytes) -> None:
    target = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(data)


def build_change_set(writes: Iterable[object], workspace_root: str) -> Tuple[Tuple[ChangedPath, ...], Dict[str, bytes]]:
    """The committed batch as a change set, and each changed path's PRE bytes.

    ``writes`` are the StagedFileWrite objects the commit will receive. The
    PRE content of a changed path is the real workspace file, accepted only
    when its revision equals the one the commit transaction is grounded on
    (``expected_base_revision`` is a content hash, not something to read
    back from). Raises ScopeError(BASELINE_IDENTITY_MISMATCH) otherwise."""
    changes: List[ChangedPath] = []
    pre_bytes: Dict[str, bytes] = {}
    for write in writes:
        relpath = workspace_relpath(write.target_path, workspace_root)
        disk = _read_regular(os.path.join(workspace_root, relpath))
        expected = write.expected_base_revision
        base_exists = write.expected_base_exists
        if disk is None:
            if write.delete or base_exists is True or expected != content_revision(""):
                raise ScopeError(
                    BASELINE_IDENTITY_MISMATCH,
                    f"{relpath}: the base the commit is grounded on is not present in the workspace",
                )
            kind = ChangeKind.ADDED
        else:
            if base_exists is False or content_revision(disk.decode("utf-8", errors="replace")) != expected:
                raise ScopeError(
                    BASELINE_IDENTITY_MISMATCH,
                    f"{relpath}: the workspace no longer holds the base revision the commit is grounded on",
                )
            pre_bytes[relpath] = disk
            kind = ChangeKind.DELETED if write.delete else ChangeKind.MODIFIED
        post = None
        if kind is not ChangeKind.DELETED:
            post = write.content_bytes if write.content_bytes is not None else write.content.encode("utf-8")
        changes.append(ChangedPath(relpath=relpath, kind=kind, post_bytes=post, expected_base_revision=expected))
    return tuple(sorted(changes, key=lambda c: c.relpath)), pre_bytes


def is_excluded(relpath: str, patterns: Sequence[str]) -> bool:
    """A path matches when it, or any of its parent directories, matches."""
    parts = relpath.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(prefix, pattern.rstrip("/")) for pattern in patterns for prefix in prefixes)


def module_root(workspace_root: str, relpath: str) -> str:
    """The nearest existing ancestor directory holding a build descriptor
    (resolved on the base); the workspace root ("") when there is none."""
    directory = os.path.dirname(relpath)
    while directory:
        absolute = os.path.join(workspace_root, directory)
        if os.path.isdir(absolute) and any(
            os.path.isfile(os.path.join(absolute, name)) for name in BUILD_DESCRIPTORS
        ):
            return directory
        directory = os.path.dirname(directory)
    return ""


def _outermost(roots: Iterable[str]) -> Tuple[str, ...]:
    ordered = sorted(set(roots), key=lambda r: (r.count("/") if r else -1, r))
    kept: List[str] = []
    for root in ordered:
        if not any(k == "" or root == k or root.startswith(k + "/") for k in kept):
            kept.append(root)
    return tuple(sorted(kept))


def resolve_roots(
    kind: ScanScope, workspace_root: str, changes: Sequence[ChangedPath], build_graph_roots: Sequence[str] = (),
) -> Tuple[str, ...]:
    if kind is ScanScope.CHANGED_FILES:
        return ()
    if kind is ScanScope.REPOSITORY:
        return ("",)
    roots = {module_root(workspace_root, c.relpath) for c in changes}
    if kind is ScanScope.BUILD_GRAPH:
        real_workspace = os.path.realpath(workspace_root)
        for root in build_graph_roots:
            relative = _safe_relpath(root)
            real = os.path.realpath(os.path.join(workspace_root, relative))
            if os.path.commonpath((real_workspace, real)) != real_workspace:
                raise ScopeError(SCOPE_UNRESOLVABLE, f"build-graph root {root!r} resolves outside the workspace")
            roots.add("" if relative == "." else relative)
    return _outermost(roots)


def _walk_sources(workspace_root: str, roots: Sequence[str]) -> Iterator[str]:
    """Classified source files under the roots on the base (no symlinks,
    no VCS or Kriya state)."""
    for root in roots:
        for directory, dirnames, filenames in os.walk(os.path.join(workspace_root, root)):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _SKIP_DIRS and not os.path.islink(os.path.join(directory, d))
            )
            for name in sorted(filenames):
                absolute = os.path.join(directory, name)
                relpath = os.path.relpath(absolute, workspace_root).replace(os.sep, "/")
                if language_of(relpath) is not None and not os.path.islink(absolute):
                    yield relpath


@dataclass(frozen=True)
class ScopeInputs:
    kind: ScanScope
    provider_minimum: ScanScope
    roots: Tuple[str, ...]
    exclusions: Tuple[str, ...]
    control_files: Tuple[str, ...]
    max_target_bytes: int


def build_scope(
    inputs: ScopeInputs, changes: Sequence[ChangedPath], pre_bytes: Mapping[str, bytes], workspace_root: str,
    *, pre_dir: Optional[str] = None, post_dir: Optional[str] = None,
) -> ScopePlan:
    """Hash the scope and, when directories are given, materialize the PRE
    and POST snapshots in the same pass. Without directories it only
    recomputes the scope identity (the commit-time re-check)."""
    changed = {c.relpath: c for c in changes}
    candidates: Set[str] = set(_walk_sources(workspace_root, inputs.roots)) | set(changed)
    pre_members: Dict[str, str] = {}
    post_members: Dict[str, str] = {}
    pre_targets: List[str] = []
    post_targets: List[str] = []
    excluded: List[str] = []
    oversized: List[OversizedTarget] = []
    limit = inputs.max_target_bytes

    def admit(relpath: str, side: Side, data: bytes, members: Dict[str, str], targets: List[str], root: Optional[str]) -> None:
        members[relpath] = bytes_digest(data)
        if language_of(relpath) is None:
            return
        if len(data) > limit:
            oversized.append(OversizedTarget(relpath=relpath, side=side, size=len(data), limit=limit))
            return
        targets.append(relpath)
        if root is not None:
            _write_bytes(root, relpath, data)

    for relpath in sorted(candidates):
        if os.path.basename(relpath) in inputs.control_files or is_excluded(relpath, inputs.exclusions):
            excluded.append(relpath)
            continue
        change = changed.get(relpath)
        if change is None:
            data = _read_regular(os.path.join(workspace_root, relpath))
            if data is None:
                continue
            admit(relpath, Side.PRE, data, pre_members, pre_targets, pre_dir)
            admit(relpath, Side.POST, data, post_members, post_targets, post_dir)
            continue
        if change.kind is not ChangeKind.ADDED:
            admit(relpath, Side.PRE, pre_bytes[relpath], pre_members, pre_targets, pre_dir)
        if change.kind is not ChangeKind.DELETED:
            admit(relpath, Side.POST, change.post_bytes, post_members, post_targets, post_dir)

    return ScopePlan(
        kind=inputs.kind, provider_minimum=inputs.provider_minimum,
        pre_members=pre_members, post_members=post_members,
        pre_targets=tuple(pre_targets), post_targets=tuple(post_targets),
        changed=tuple(changes), module_roots=inputs.roots, exclusions=inputs.exclusions,
        excluded=tuple(excluded), oversized=tuple(oversized), max_target_bytes=limit,
    )

"""Filesystem path identity (ARCH-PLATFORM-001, PLAT-001/PLAT-002).

Two different path strings can name one file: an ASCII case variant or a
Unicode normalization variant (NFC/NFD) on a case- or normalization-
insensitive filesystem (macOS APFS by default, Windows NTFS, Linux ext4
casefold directories), a symlink alias, or a hard link. A security decision
that compares path strings then fails open. This module answers identity
questions from the filesystem itself - ``os.stat`` identity (device + inode,
``os.path.samestat``) - never from the OS name and never from a string
compare of paths that exist.

Every function is side-effect-free: only ``realpath``/``stat``, no probe
file is ever created. Each answer is three-valued; UNKNOWN means identity
could not be established (a stat failed for a reason other than "does not
exist"), and the caller must fail closed in its own direction.

Paths that do not exist yet are compared by their nearest existing
ancestor's identity plus the remaining components folded for case and
Unicode normalization. The fold is conservative: it can make two names
compare equal on a case-sensitive filesystem where they would be different
files, never the reverse.
"""
from __future__ import annotations

import enum
import os
import stat
import unicodedata
from typing import Optional, Tuple


class PathRelation(enum.Enum):
    WITHIN = "within"   # the target is the root or beneath it
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


class PathIdentity(enum.Enum):
    SAME = "same"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


def fold_name(name: str) -> str:
    """Case- and normalization-insensitive form of a path string."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", name).casefold())


def _stat(path: str) -> Tuple[Optional[os.stat_result], bool]:
    """(stat, known): (result, True) when it exists, (None, True) when it
    does not, (None, False) when existence cannot be established."""
    try:
        return os.stat(path), True
    except (FileNotFoundError, NotADirectoryError):
        return None, True
    except (OSError, ValueError):
        return None, False


def _existing_prefix(real_path: str) -> Tuple[Optional[str], Optional[os.stat_result], Tuple[str, ...]]:
    """The nearest existing ancestor of ``real_path`` (itself included), its
    stat, and the non-existent components below it. (None, None, ()) when
    existence cannot be established."""
    tail = []
    current = real_path
    while True:
        result, known = _stat(current)
        if not known:
            return None, None, ()
        if result is not None:
            return current, result, tuple(reversed(tail))
        parent = os.path.dirname(current)
        if parent == current:
            return None, None, ()
        tail.append(os.path.basename(current))
        current = parent


def _lexically_within(root: str, target: str) -> bool:
    root, target = fold_name(root), fold_name(target)
    return target == root or target.startswith(root.rstrip(os.sep) + os.sep)


def path_relation(root: str, target: str) -> PathRelation:
    """Whether ``target`` is ``root`` or lies beneath it, by filesystem
    identity: the target's nearest existing ancestor chain is walked up and
    each directory compared to the root with ``samestat``, so case,
    normalization and symlink aliases of the root are all recognized. A
    root that does not exist is compared lexically with folding."""
    try:
        real_root, real_target = os.path.realpath(root), os.path.realpath(target)
    except (OSError, ValueError):
        return PathRelation.UNKNOWN
    root_stat, known = _stat(real_root)
    if not known:
        return PathRelation.UNKNOWN
    if root_stat is None:
        return PathRelation.WITHIN if _lexically_within(real_root, real_target) else PathRelation.OUTSIDE
    current, current_stat, _tail = _existing_prefix(real_target)
    if current is None:
        return PathRelation.UNKNOWN
    while True:
        if os.path.samestat(current_stat, root_stat):
            return PathRelation.WITHIN
        parent = os.path.dirname(current)
        if parent == current:
            return PathRelation.OUTSIDE
        current_stat, known = _stat(parent)
        if not known or current_stat is None:
            return PathRelation.UNKNOWN
        current = parent


def identity_key(path: str) -> Optional[Tuple[int, int]]:
    """Hashable filesystem identity of an existing path: the (device, inode)
    pair ``samestat`` compares, so every alias of one directory maps to one
    key. None when the path does not exist or cannot be stat'd."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return None
    result, _known = _stat(real)
    if result is None:
        return None
    return (result.st_dev, result.st_ino)


class LinkSharing(enum.Enum):
    """OI-164: whether another directory entry can name the same filesystem object as a path (a hard link)."""
    SINGLE = "single"    # absent, a directory, or a non-directory with one link: no other name refers to it
    SHARED = "shared"    # a non-directory with more than one link: another name may be a protected object's
    UNKNOWN = "unknown"  # the stat failed, or the host gives no usable identity (st_ino 0): the caller fails closed


def object_key(result: os.stat_result) -> Optional[Tuple[int, int]]:
    """The (device, inode) identity of a stat result - what ``samestat`` compares; the device is part of the key
    because inode numbers repeat across filesystems (a hard link never crosses one). None when the host gives no
    usable identity (st_ino 0: some network and FUSE filesystems, or a DirEntry stat on Windows)."""
    if not result.st_ino:
        return None
    return (result.st_dev, result.st_ino)


def link_sharing(path: str) -> Tuple[LinkSharing, Optional[Tuple[int, int]]]:
    """Whether ``path`` (symlinks followed, as reading it would) names a non-directory object that has more than one
    directory entry, and if so its ``object_key``. A directory is never SHARED: its st_nlink counts its
    subdirectories, and a directory alias is a symlink (resolved by ``path_relation``), not a hard link."""
    try:
        result = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return LinkSharing.SINGLE, None
    except (OSError, ValueError):
        return LinkSharing.UNKNOWN, None
    if stat.S_ISDIR(result.st_mode) or result.st_nlink <= 1:
        return LinkSharing.SINGLE, None
    key = object_key(result)
    return (LinkSharing.SHARED, key) if key is not None else (LinkSharing.UNKNOWN, None)


def path_identity(first: str, second: str) -> PathIdentity:
    """Whether two paths name the same file. Existing paths are compared by
    ``samestat`` (hard links and every alias included). A path that exists
    never names the same file as one that does not (on a case-insensitive
    filesystem a variant of an existing name exists). Two non-existent
    paths are the same when their nearest existing ancestors are the same
    directory and the remaining components fold equal."""
    try:
        real_first, real_second = os.path.realpath(first), os.path.realpath(second)
    except (OSError, ValueError):
        return PathIdentity.UNKNOWN
    first_stat, first_known = _stat(real_first)
    second_stat, second_known = _stat(real_second)
    if not (first_known and second_known):
        return PathIdentity.UNKNOWN
    if first_stat is not None and second_stat is not None:
        return PathIdentity.SAME if os.path.samestat(first_stat, second_stat) else PathIdentity.DIFFERENT
    if first_stat is not None or second_stat is not None:
        return PathIdentity.DIFFERENT
    first_base, first_base_stat, first_tail = _existing_prefix(real_first)
    second_base, second_base_stat, second_tail = _existing_prefix(real_second)
    if first_base is None or second_base is None:
        return PathIdentity.UNKNOWN
    if not os.path.samestat(first_base_stat, second_base_stat):
        return PathIdentity.DIFFERENT
    same_tail = tuple(map(fold_name, first_tail)) == tuple(map(fold_name, second_tail))
    return PathIdentity.SAME if same_tail else PathIdentity.DIFFERENT


def _same_object(first: str, second: str) -> bool:
    """The FILESYSTEM says both spellings name one object (HOSTED-CANONICAL-SPELLING-001). A name that merely case-folds to an existing entry is the same directory only
    where the filesystem resolves it so (macOS APFS default, Windows, casefold mounts); on a case-sensitive filesystem ``mixedcase`` is a different, possibly non-existent
    path and must keep its own spelling, or two distinct paths would share one persisted identity. Asked of the filesystem itself, never inferred from an OS name."""
    first_stat, _ = _stat(first)
    second_stat, _ = _stat(second)
    return first_stat is not None and second_stat is not None and os.path.samestat(first_stat, second_stat)


def canonical_spelling(path: str) -> str:
    """PLAT-017 (BACKEND-READINESS-004): the real path re-spelled exactly as
    the filesystem stores each existing component, so every case or
    normalization variant of one directory yields one string on a
    case-insensitive filesystem (and a case-sensitive one is unchanged:
    only an exact entry matches there - a differently cased name is folded into an
    existing entry only when the filesystem itself resolves both to one object). Components that do not exist keep
    the spelling given. Used for identities that are persisted and
    compared, never for authority decisions (``path_relation`` does those)."""
    try:
        real = os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        return path
    drive, tail = os.path.splitdrive(real)
    parts = [p for p in tail.split(os.sep) if p]
    current = drive + os.sep if tail.startswith(os.sep) else drive or ""
    spelled = []
    for part in parts:
        candidate = os.path.join(current, part) if current else part
        try:
            entries = os.listdir(current or ".")
        except OSError:
            spelled.append(part)
            current = candidate
            continue
        actual = part if part in entries else next((e for e in entries if fold_name(e) == fold_name(part) and _same_object(candidate, os.path.join(current, e) if current else e)), part)
        spelled.append(actual)
        current = os.path.join(current, actual) if current else actual
    return (drive + os.sep if tail.startswith(os.sep) else drive) + os.sep.join(spelled) if spelled else real

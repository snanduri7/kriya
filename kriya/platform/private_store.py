"""The ONE owner of "private Kriya store" permissions (WAVE8 OI-134 / OI-154, OBS-003 / KRP-025; owner policy 2026-11-06).

Kriya keeps material that can hold a credential or security-authority state in stores whose bytes must stay EXACT (their digests bind them: attempt-evidence
content blobs, verification contracts, checkpoints, RunRecords, milestone state, launch records, the trace database). Redacting those at write would break
resume / export / digest binding, so the contract for them is protection, not scrubbing; everything NOT bound by a digest is redacted at its writer through the
one redactor (kriya/foundation/redaction.py). This module is the protection half, one rule for every store:

* a private DIRECTORY is a real directory (never a symlink) owned by this user with mode ``0700``;
* a private FILE is a regular file (never a symlink) owned by this user with mode ``0600``;
* an artifact written by an earlier Kriya that this user owns and that is wider is TIGHTENED (``chmod``) - legacy stores are corrected, not trusted;
* an artifact owned by ANYONE ELSE, or a symlink where a directory/file is required, is REFUSED (``PrivateStoreUnsafeError``): Kriya never adopts, chmods or
  writes into a store it does not own.

Platform mechanism, so it lives here (PLATFORM): ownership comes from the host-identity port (no OS call in the callers). Windows has no POSIX mode bits and no
supported Kriya runtime (PLAT-WINDOWS-PROVIDERS-001): every function is a no-op there, stated, not claimed. Pure standard library + the port; it never reads or
writes file CONTENT.
"""
from __future__ import annotations

import os
import stat

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_GROUP_OTHER = 0o077


class PrivateStoreUnsafeError(RuntimeError):
    """A private store cannot be established as this user's own real directory/file (a symlink, a wrong kind, or another owner's artifact)."""


def _active() -> bool:
    return os.name != "nt"


def _current_uid() -> int:
    from kriya.platform.services import platform_services

    return platform_services().host_identity.current().uid


def ensure_private_directory(path: str, *, create: bool = True) -> bool:
    """``path`` is, on return, a real directory owned by this user with mode 0700. Created (mode 0700 via ``chmod`` after ``mkdir``, so neither a restrictive
    nor a permissive umask changes it) when missing and ``create``; a wider one this user owns is tightened. Returns True when it exists afterwards (False only
    for a missing directory with ``create=False``); missing parents are created private first. Raises ``PrivateStoreUnsafeError`` for a symlink, a non-directory or another owner's directory."""
    if not _active():
        return os.path.isdir(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            return False
        parent = os.path.dirname(os.path.abspath(path))
        if parent != path and not os.path.lexists(parent):
            ensure_private_directory(parent)   # a parent Kriya has to create is Kriya's own: private too; an existing parent is never touched
        try:
            os.mkdir(path, PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as error:
            raise PrivateStoreUnsafeError(f"cannot create the private directory {path!r}: {error}") from error
        info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise PrivateStoreUnsafeError(f"{path!r} is not a real directory (a symlink or a file): Kriya will not use it as a private store")
    if info.st_uid != _current_uid():
        raise PrivateStoreUnsafeError(f"{path!r} is owned by uid {info.st_uid}, not by this user: Kriya will not adopt or write into it")
    if stat.S_IMODE(info.st_mode) != PRIVATE_DIRECTORY_MODE:
        try:
            os.chmod(path, PRIVATE_DIRECTORY_MODE)
        except OSError as error:
            raise PrivateStoreUnsafeError(f"cannot restrict {path!r} to its owner: {error}") from error
    return True


def ensure_private_directories(root: str, path: str) -> None:
    """Every directory from ``root`` down to ``path`` (both included; ``path`` must be ``root`` or beneath it) is private, created as needed. ``root`` itself is
    created/validated like any private directory; its own parents are NOT touched (the caller names the first Kriya-owned directory)."""
    root, path = os.path.abspath(root), os.path.abspath(path)
    # beneath-ness is judged on the RESOLVED spellings (macOS /var -> /private/var; a caller may hold either), the chain is built from the root as given, and
    # ensure_private_directory refuses a root or a component that is itself a symlink (lstat), so resolution here never lets a link redirect a chmod.
    real_root, real_path = os.path.realpath(root), os.path.realpath(path)
    if os.path.commonpath([real_root, real_path]) != real_root:
        raise PrivateStoreUnsafeError(f"{path!r} is not beneath the private root {root!r}")
    ensure_private_directory(root)
    relative = os.path.relpath(real_path, real_root)
    current = root
    if relative != ".":
        for part in relative.split(os.sep):
            current = os.path.join(current, part)
            ensure_private_directory(current)


def ensure_private_file(path: str) -> bool:
    """An EXISTING ``path`` is, on return, a regular file owned by this user with mode 0600 (tightened when wider). Returns False when it does not exist.
    Raises ``PrivateStoreUnsafeError`` for a symlink, a non-regular file or another owner's file."""
    if not _active():
        return os.path.isfile(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        raise PrivateStoreUnsafeError(f"{path!r} is not a regular file (a symlink or another kind): Kriya will not use it as a private store file")
    if info.st_uid != _current_uid():
        raise PrivateStoreUnsafeError(f"{path!r} is owned by uid {info.st_uid}, not by this user: Kriya will not adopt or replace it")
    if stat.S_IMODE(info.st_mode) & _GROUP_OTHER or not stat.S_IMODE(info.st_mode) & stat.S_IRUSR:
        try:
            os.chmod(path, PRIVATE_FILE_MODE)
        except OSError as error:
            raise PrivateStoreUnsafeError(f"cannot restrict {path!r} to its owner: {error}") from error
    return True


def require_own_replaceable(path: str) -> None:
    """Before a private write REPLACES ``path``: a missing target is fine; an existing one must be this user's regular file (never a symlink, never another
    owner's). Does not change its mode - the replacement is created 0600."""
    if not _active():
        return
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != _current_uid():
        raise PrivateStoreUnsafeError(f"{path!r} is not this user's own regular file: Kriya will not replace it with private content")

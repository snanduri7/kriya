"""REPOSITORY: byte-exact file snapshots and raw-byte revision identity (full modularization, slice M1;
MODULE-CONTRACTS §11; FILE-INTEGRITY-CONTRACT-001).

Moved verbatim from kriya/workflow/file_integrity.py, which re-exports every name until P1 and keeps the one edit
engine (``apply_line_block_edits``/``mutate_snapshot``, GENERATION) and the verification tree binding (AUTHORITY):
``load_snapshot`` reads the raw bytes once (their SHA-256 is the revision, ``raw_digest``); the text view is strict
UTF-8 (with or without BOM) with one newline convention, proven lossless, else a typed refusal
(``UNSUPPORTED_TEXT_ENCODING`` / ``MIXED_NEWLINE_UNSUPPORTED`` / ``SYMLINK_TARGET_UNSUPPORTED``);
``FileSnapshot.encode`` writes text back in the file's own convention; ``require_unchanged`` refuses a source that
changed since it was authorized; the stat/content identities and ``untracked_repository_paths`` (Git's own
untracked-and-not-ignored listing) are what the verification tree binding compares. Every refusal is a
``FileIntegrityError`` whose message starts with its reason code.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass, replace
from typing import Optional, Tuple

from kriya.platform import git_argv

UNSUPPORTED_TEXT_ENCODING = "UNSUPPORTED_TEXT_ENCODING"
MIXED_NEWLINE_UNSUPPORTED = "MIXED_NEWLINE_UNSUPPORTED"
SOURCE_CHANGED_SINCE_AUTHORIZATION = "SOURCE_CHANGED_SINCE_AUTHORIZATION"
SYMLINK_TARGET_UNSUPPORTED = "SYMLINK_TARGET_UNSUPPORTED"

UTF8_BOM = b"\xef\xbb\xbf"

LF, CRLF, NO_NEWLINE, MIXED = "lf", "crlf", "none", "mixed"


class FileIntegrityError(ValueError):
    """A typed refusal; ``str(error)`` is ``"<REASON_CODE>: <detail>"``."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


def raw_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_raw_digest(path: str) -> str:
    """The revision of the file at ``path``: SHA-256 of its raw bytes, the
    digest of no bytes when it does not exist."""
    try:
        with open(path, "rb") as handle:
            return raw_digest(handle.read())
    except FileNotFoundError:
        return raw_digest(b"")


def display_text(data: bytes) -> str:
    """A human/scanner view of ``data``. Never mutation or revision
    authority: undecodable bytes are shown as U+FFFD, and every decision is
    taken on the raw bytes (``raw_digest``) instead."""
    return data.decode("utf-8", errors="replace")


def newline_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return MIXED
    if crlf:
        return CRLF
    return LF if lf else NO_NEWLINE


@dataclass(frozen=True)
class FileSnapshot:
    """One immutable read of a file: the raw bytes and what they are."""

    path: str
    exists: bool
    raw_bytes: bytes
    raw_sha256: str
    bom: bool
    newline: str
    has_final_newline: bool
    file_mode: Optional[int]
    is_symlink: bool
    encoding: Optional[str]
    encoding_error: Optional[str] = None

    @property
    def text(self) -> str:
        """The strict, LF-normalized text the edit engine works on."""
        self.require_mutable()
        body = self.raw_bytes[len(UTF8_BOM):] if self.bom else self.raw_bytes
        decoded = body.decode("utf-8")
        return decoded.replace("\r\n", "\n") if self.newline == CRLF else decoded

    def require_mutable(self) -> None:
        if self.is_symlink:
            raise FileIntegrityError(
                SYMLINK_TARGET_UNSUPPORTED,
                f"'{self.path}' is a symbolic link; Kriya does not replace a link with a regular file",
            )
        if self.encoding is None:
            raise FileIntegrityError(
                UNSUPPORTED_TEXT_ENCODING,
                f"'{self.path}' is not valid UTF-8 ({self.encoding_error}); the file is left untouched",
            )
        if self.newline == MIXED:
            raise FileIntegrityError(
                MIXED_NEWLINE_UNSUPPORTED,
                f"'{self.path}' mixes CRLF and LF line endings; the file is left untouched",
            )

    def encode(self, text: str) -> bytes:
        """``text`` as bytes in this file's own convention.

        For an existing file every line break of ``text`` (LF, or a CR LF the
        model wrote) is expressed in the file's convention - the only way the
        LF-based edit engine can keep a CRLF file CRLF; a lone CR is payload
        and kept. A file with no newline yet is written LF. A new file keeps
        the payload's own convention, and a payload mixing CR LF and LF there
        is refused rather than normalized."""
        if self.exists:
            body = text.replace("\r\n", "\n")
            if self.newline == CRLF:
                body = body.replace("\n", "\r\n")
        else:
            body = text
        data = (UTF8_BOM if self.bom else b"") + body.encode("utf-8")
        if newline_style(data) == MIXED:
            raise FileIntegrityError(
                MIXED_NEWLINE_UNSUPPORTED,
                f"the content for '{self.path}' mixes CRLF and LF line endings; it is refused, never normalized",
            )
        return data


def load_snapshot(path: str) -> FileSnapshot:
    """Read ``path`` once. A missing file is an empty, non-existent snapshot."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return FileSnapshot(
            path=path, exists=False, raw_bytes=b"", raw_sha256=raw_digest(b""), bom=False,
            newline=NO_NEWLINE, has_final_newline=False, file_mode=None, is_symlink=False,
            encoding="utf-8",
        )
    is_link = stat.S_ISLNK(info.st_mode)
    if is_link:
        # A link's identity is its target text; it is never read through.
        data = os.fsencode(os.readlink(path))
        return FileSnapshot(
            path=path, exists=True, raw_bytes=data, raw_sha256=raw_digest(data), bom=False,
            newline=NO_NEWLINE, has_final_newline=False, file_mode=stat.S_IMODE(info.st_mode),
            is_symlink=True, encoding=None, encoding_error="symbolic link",
        )
    with open(path, "rb") as handle:
        data = handle.read()
    bom = data.startswith(UTF8_BOM)
    encoding: Optional[str] = "utf-8-sig" if bom else "utf-8"
    error = None
    try:
        (data[len(UTF8_BOM):] if bom else data).decode("utf-8")
    except UnicodeDecodeError as decode_error:
        encoding, error = None, f"invalid byte at offset {decode_error.start}"
    snapshot = FileSnapshot(
        path=path, exists=True, raw_bytes=data, raw_sha256=raw_digest(data), bom=bom,
        newline=newline_style(data), has_final_newline=data.endswith(b"\n"),
        file_mode=stat.S_IMODE(info.st_mode), is_symlink=False,
        encoding=encoding, encoding_error=error,
    )
    if encoding is not None and snapshot.newline != MIXED and snapshot.encode(snapshot.text) != data:
        # The text view is proven lossless, never assumed: a file it cannot
        # reproduce byte for byte (a stray CR before a CRLF, say) is not
        # mutable text.
        return replace(snapshot, newline=MIXED)
    return snapshot


def read_shown_text(path: str) -> Tuple[str, str]:
    """(text, shown revision) of the file at ``path`` for a prompt or a
    context-freshness decision. The revision is the digest of the strict,
    LF-normalized text when the file is mutable text - equal to
    content_revision() of that text - else ``"raw:" + raw digest``, which
    no decoded view can ever equal. Raises FileNotFoundError when absent."""
    snapshot = load_snapshot(path)
    if not snapshot.exists:
        raise FileNotFoundError(path)
    try:
        text = snapshot.text
    except FileIntegrityError:
        return display_text(snapshot.raw_bytes), "raw:" + snapshot.raw_sha256
    return text, raw_digest(text.encode("utf-8"))


def require_unchanged(snapshot: FileSnapshot, authorized_digest: str) -> None:
    """Refuse a mutation whose source changed since it was authorized."""
    if snapshot.raw_sha256 != authorized_digest:
        raise FileIntegrityError(
            SOURCE_CHANGED_SINCE_AUTHORIZATION,
            f"'{snapshot.path}' changed since the mutation was authorized "
            f"({authorized_digest[:12]} -> {snapshot.raw_sha256[:12]}); no edit is applied",
        )


def _stat_identity(path: str) -> Optional[Tuple[int, int, int, int, int]]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    return (info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _content_identity(path: str) -> Optional[str]:
    """What the tree holds at ``path``: absent (None), a link (its target),
    or a file (its permission bits and raw-byte digest)."""
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    if os.path.isdir(path):
        return "dir"
    mode = stat.S_IMODE(os.lstat(path).st_mode)
    return f"{mode:o}:{file_raw_digest(path)}"


def untracked_repository_paths(root: str) -> frozenset:
    """Every file under ``root`` that Git neither tracks nor ignores (its own
    exclude semantics: .gitignore, info/exclude, core.excludesFile), Kriya's
    ``.kriya/`` state excluded. Not a Git work tree: none. A failing listing
    raises (fail closed)."""
    inside = subprocess.run(git_argv(["git", "rev-parse", "--is-inside-work-tree"]), cwd=root, capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return frozenset()
    result = subprocess.run(git_argv(["git", "ls-files", "--others", "--exclude-standard", "-z"]), cwd=root,
                            capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files --others failed: {os.fsdecode(result.stderr).strip() or result.returncode}")
    return frozenset(path for path in (os.fsdecode(item) for item in result.stdout.split(b"\0") if item)
                     if path != ".kriya" and not path.startswith(".kriya/"))

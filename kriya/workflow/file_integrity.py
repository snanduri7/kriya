"""FILE-INTEGRITY-CONTRACT-001: byte-exact file snapshots and the one edit engine.

Invariant: Kriya never silently alters file bytes outside the exact authorized
mutation. A mutation of an existing file goes through three steps:

1. ``load_snapshot`` reads the raw bytes once. Their SHA-256 is the file's
   revision identity (``raw_digest``); nothing is decoded with replacement
   characters. The text view is strict UTF-8 (with or without BOM), with one
   newline convention (LF or CRLF). Anything else is refused typed
   (``UNSUPPORTED_TEXT_ENCODING`` / ``MIXED_NEWLINE_UNSUPPORTED``), and a
   snapshot is only usable when ``encode(text) == raw_bytes`` is proven.
2. ``apply_line_block_edits`` locates every SEARCH block as a run of complete
   lines (split on "\\n" only - form feed, U+2028 and friends are payload),
   against the same source, and rejects zero, ambiguous and overlapping
   anchors before splicing anything. Bytes outside the matched spans are the
   source's own.
3. ``FileSnapshot.encode`` turns the result back into bytes with the source's
   BOM and newline convention, and refuses a result whose newlines no longer
   match that convention.

Every failure is a ``FileIntegrityError`` whose message starts with its reason
code (``"ANCHOR_NOT_FOUND: ..."``), the shape the attempt's anchored-edit
failure handling already records as ``diagnostics.reason_code``.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.platform.filesystem_semantics import PathIdentity, PathRelation, path_identity, path_relation
from kriya.workflow.failure import Failure, QualityGateFailure

EMPTY_SEARCH_BLOCK = "EMPTY_SEARCH_BLOCK"
ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
ANCHOR_NOT_IN_FILE = "ANCHOR_NOT_IN_FILE"
OVERLAPPING_EDITS = "OVERLAPPING_EDITS"
INDENTATION_STYLE_MISMATCH = "INDENTATION_STYLE_MISMATCH"
UNSUPPORTED_TEXT_ENCODING = "UNSUPPORTED_TEXT_ENCODING"
MIXED_NEWLINE_UNSUPPORTED = "MIXED_NEWLINE_UNSUPPORTED"
SOURCE_CHANGED_SINCE_AUTHORIZATION = "SOURCE_CHANGED_SINCE_AUTHORIZATION"
SYMLINK_TARGET_UNSUPPORTED = "SYMLINK_TARGET_UNSUPPORTED"
WORKTREE_SYNC_FAILED = "WORKTREE_SYNC_FAILED"
WORKTREE_CONTENT_MISMATCH = "WORKTREE_CONTENT_MISMATCH"

# Reason codes that no Developer retry can change: the file itself is outside
# what the edit engine can mutate byte-exactly.
DETERMINISTIC_FILE_INTEGRITY_STOPS = frozenset({
    UNSUPPORTED_TEXT_ENCODING, MIXED_NEWLINE_UNSUPPORTED, SYMLINK_TARGET_UNSUPPORTED,
})

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


def keep_final_newline_state(snapshot: FileSnapshot, content: str) -> str:
    """A whole-file replacement of an existing file keeps that file's final
    newline presence or absence (a file convention, like its line endings).
    An empty file has no such convention: the content is kept as given."""
    if not snapshot.exists or not snapshot.raw_bytes or not content:
        return content
    if snapshot.has_final_newline and not content.endswith("\n"):
        return content + "\n"
    if not snapshot.has_final_newline and content.endswith("\n"):
        return content[:-1]
    return content


# ---------------------------------------------------------------- edit engine


@dataclass(frozen=True)
class AnchoredReplace:
    """Replace the unique complete-line block ``search`` with ``replace``."""

    operation_id: str
    search: str
    replace: str


@dataclass(frozen=True)
class AppliedEdit:
    operation_id: str
    start_line: int  # 0-based, inclusive, in the source
    end_line: int    # exclusive
    tolerant: bool


@dataclass(frozen=True)
class EditResult:
    text: str
    applied: Tuple[AppliedEdit, ...]


def _block_lines(block: str) -> List[str]:
    """A SEARCH/REPLACE payload as lines; one trailing newline ends the last
    line rather than adding an empty one."""
    if block.endswith("\n"):
        block = block[:-1]
    return block.split("\n")


def _leading(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def _source_lines(text: str) -> Tuple[List[str], int]:
    lines = text.split("\n")
    matchable = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    return lines, matchable


def _find(lines: Sequence[str], matchable: int, search: Sequence[str], key) -> List[int]:
    wanted = [key(line) for line in search]
    width = len(wanted)
    return [
        start for start in range(0, matchable - width + 1)
        if all(key(lines[start + offset]) == wanted[offset] for offset in range(width))
    ]


def _indent_transform(source: Sequence[str], search: Sequence[str], operation_id: str) -> Tuple[str, str]:
    """("add"|"remove", prefix) mapping the SEARCH indentation onto the
    source's; tabs vs spaces or an inconsistent shift is refused."""
    pairs = [(_leading(f), _leading(s)) for f, s in zip(source, search, strict=True) if f.strip(" \t")]
    for mode in ("add", "remove"):
        prefixes = set()
        for file_lead, search_lead in pairs:
            outer, inner = (file_lead, search_lead) if mode == "add" else (search_lead, file_lead)
            if not outer.endswith(inner):
                break
            prefixes.add(outer[:len(outer) - len(inner)])
        else:
            if len(prefixes) <= 1:
                return mode, next(iter(prefixes), "")
    raise FileIntegrityError(
        INDENTATION_STYLE_MISMATCH,
        f"edit {operation_id}: the SEARCH block matches only when indentation is ignored, and its "
        "indentation cannot be mapped onto the file's by one consistent shift (tabs vs spaces?)",
    )


def _reindent(replace: List[str], mode: str, prefix: str, file_indent_chars: set, operation_id: str) -> List[str]:
    result = []
    for line in replace:
        if not line.strip(" \t"):
            result.append(line)
            continue
        if mode == "add":
            line = prefix + line
        elif not line.startswith(prefix):
            raise FileIntegrityError(
                INDENTATION_STYLE_MISMATCH,
                f"edit {operation_id}: a REPLACE line is indented less than the shift applied to its SEARCH block",
            )
        else:
            line = line[len(prefix):]
        if file_indent_chars and not set(_leading(line)) <= file_indent_chars:
            raise FileIntegrityError(
                INDENTATION_STYLE_MISMATCH,
                f"edit {operation_id}: the REPLACE indentation mixes tabs and spaces against the file's own",
            )
        result.append(line)
    return result


def apply_line_block_edits(text: str, edits: Sequence[AnchoredReplace]) -> EditResult:
    """Apply every edit to ``text`` (LF-normalized) as complete-line blocks.

    All anchors are located in the same source first: exact line equality,
    else (only when no exact match exists) equality ignoring leading and
    trailing spaces/tabs on each line, blank lines still matching blank lines
    one for one. Exactly one match is required; overlapping edits are
    refused. Nothing is spliced until every edit has located."""
    lines, matchable = _source_lines(text)
    located = []
    for edit in edits:
        search = _block_lines(edit.search)
        if not edit.search or not any(line.strip() for line in search):
            raise FileIntegrityError(
                EMPTY_SEARCH_BLOCK,
                f"edit {edit.operation_id}: the SEARCH block is empty; an insertion needs an explicit anchor",
            )
        replace = _block_lines(edit.replace) if edit.replace else []
        exact = _find(lines, matchable, search, lambda line: line)
        tolerant = False
        if not exact:
            exact = _find(lines, matchable, search, lambda line: line.strip(" \t"))
            tolerant = True
        if not exact:
            raise FileIntegrityError(
                ANCHOR_NOT_FOUND,
                f"edit {edit.operation_id}: the SEARCH block matched 0 times as a run of complete lines. "
                "Copy the exact lines from the current file (whole lines, no line-number gutter, "
                "blank lines as they are).",
            )
        if len(exact) > 1:
            raise FileIntegrityError(
                ANCHOR_AMBIGUOUS,
                f"edit {edit.operation_id}: the SEARCH block matched {len(exact)} times (must match exactly once). "
                "Include more surrounding lines.",
            )
        start = exact[0]
        end = start + len(search)
        if tolerant:
            matched = lines[start:end]
            mode, prefix = _indent_transform(matched, search, edit.operation_id)
            indent_chars = {char for line in matched for char in _leading(line)}
            replace = _reindent(replace, mode, prefix, indent_chars, edit.operation_id)
        located.append((start, end, replace, AppliedEdit(edit.operation_id, start, end, tolerant)))
    located.sort(key=lambda item: item[0])
    for previous, current in zip(located, located[1:], strict=False):
        if current[0] < previous[1]:
            raise FileIntegrityError(
                OVERLAPPING_EDITS,
                f"edits {previous[3].operation_id} and {current[3].operation_id} overlap in the source",
            )
    result = list(lines)
    for start, end, replace, _applied in reversed(located):
        result[start:end] = replace
    return EditResult(text="\n".join(result), applied=tuple(item[3] for item in located))


def anchored_replaces(edits: Sequence[Dict[str, str]]) -> List[AnchoredReplace]:
    """The Developer's parsed ``{"search", "replace"}`` dicts as typed operations."""
    return [
        AnchoredReplace(operation_id=f"#{index}", search=edit.get("search") or "",
                        replace=edit.get("replace") or "")
        for index, edit in enumerate(edits, 1)
    ]


def mutate_snapshot(snapshot: FileSnapshot, edits: Sequence[Dict[str, str]]) -> Tuple[str, bytes]:
    """(new text, new bytes) for ``edits`` applied to ``snapshot``."""
    result = apply_line_block_edits(snapshot.text, anchored_replaces(edits))
    return result.text, snapshot.encode(result.text)


# ---------------------------------------------------------------- verification tree binding

VERIFICATION_GATE_MUTATED_TRACKED_FILES = "VERIFICATION_GATE_MUTATED_TRACKED_FILES"
# FILE-INTEGRITY-CONTRACT-001B: a gate created repository content - a file Git
# neither tracks nor ignores - that is not candidate output; the tree the
# later gates verify is then not the tree that would be committed.
VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE = "VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE"
VERIFICATION_TREE_STOP_CODES = (VERIFICATION_GATE_MUTATED_TRACKED_FILES, VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE)


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


class VerificationTreeMutated(QualityGateFailure):
    """A verification gate changed repository content Kriya did not
    authorize. A deterministic stop: the verification that ran is invalid,
    and the modified bytes are never adopted as the candidate."""

    def __init__(self, gate: str, changes: Sequence[Tuple[str, Optional[str], Optional[str]]],
                 phase: str = "after") -> None:
        paths = [path for path, _expected, _actual in changes]
        where = (f"the {gate} gate modified repository content" if phase == "after"
                 else f"repository content changed between verification steps (detected before the {gate} gate)")
        message = (
            f"{VERIFICATION_GATE_MUTATED_TRACKED_FILES}: {where} Kriya did not authorize ({', '.join(paths[:10])}). "
            "Verification must run on exactly the bound tree; Kriya cannot verify one tree and commit another, "
            "so this verification is invalid."
        )
        super().__init__(Failure(
            type="verification_tree_mutated", message=message, raw_output=message, source="orchestrator",
            authority="deterministic", likely_files=paths[:50],
            diagnostics={"reason_code": VERIFICATION_GATE_MUTATED_TRACKED_FILES, "gate": gate, "phase": phase,
                         "changed_paths": paths[:200],
                         "expected_digests": {path: expected for path, expected, _ in changes[:200]},
                         "actual_digests": {path: actual for path, _, actual in changes[:200]}},
        ))
        self.gate = gate


class VerificationGateCreatedFiles(QualityGateFailure):
    """FILE-INTEGRITY-CONTRACT-001B: a verification gate created a file Git
    neither tracks nor ignores (repository content by Git's own semantics)
    that is not candidate output. A deterministic stop like
    VerificationTreeMutated: the verified tree would not be the committed
    one. Ignored build output is never repository content."""

    def __init__(self, gate: str, paths: Sequence[str], phase: str = "after") -> None:
        where = (f"the {gate} gate created" if phase == "after"
                 else f"between verification steps (detected before the {gate} gate), something created")
        message = (
            f"{VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE}: {where} repository content Kriya did not authorize - "
            f"untracked and not ignored by Git ({', '.join(list(paths)[:10])}). The tree verified would not be "
            "the tree committed; ignore real build output in the repository's .gitignore."
        )
        super().__init__(Failure(
            type="verification_tree_mutated", message=message, raw_output=message, source="orchestrator",
            authority="deterministic", likely_files=list(paths)[:50],
            diagnostics={"reason_code": VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE, "gate": gate, "phase": phase,
                         "created": [{"path": path, "tracked": False, "ignored": False} for path in list(paths)[:200]]},
        ))
        self.gate = gate


def untracked_repository_paths(root: str) -> frozenset:
    """Every file under ``root`` that Git neither tracks nor ignores (its own
    exclude semantics: .gitignore, info/exclude, core.excludesFile), Kriya's
    ``.kriya/`` state excluded. Not a Git work tree: none. A failing listing
    raises (fail closed)."""
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return frozenset()
    result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root,
                            capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files --others failed: {os.fsdecode(result.stderr).strip() or result.returncode}")
    return frozenset(path for path in (os.fsdecode(item) for item in result.stdout.split(b"\0") if item)
                     if path != ".kriya" and not path.startswith(".kriya/"))


class VerificationTreeBinding:
    """FILE-INTEGRITY-CONTRACT-001: the exact tree a verification sequence
    runs against - every repository content path (git-tracked in the
    workspace) and every candidate path, each with its stat identity and
    content identity (mode + raw digest). ``check`` after a gate re-stats
    everything and re-hashes only what changed, so a gate that rewrites a
    tracked file (a formatter bound to the build, a test fixture, the app
    under runtime verification) is caught at that gate, before any later
    gate or the commit binding sees it. Untracked, non-candidate output
    (target/, build/, caches) is not repository content and is never
    part of the binding. Kriya's own authorized writes during verification
    (self-correction patches, candidate pom corrections) re-record their
    path with ``authorize``."""

    def __init__(self, root: str, content_paths: Iterable[str], candidate_paths: Iterable[str]) -> None:
        self.root = root
        self._expected: Dict[str, Tuple[Optional[Tuple[int, int, int, int, int]], Optional[str]]] = {}
        for relpath in set(content_paths) | set(candidate_paths):
            self._record(relpath)
        # FILE-INTEGRITY-CONTRACT-001B: untracked, non-ignored content that
        # already existed is never attributed to a gate.
        self._untracked_before = untracked_repository_paths(root)
        # Output roots registered by the gate invocation now running (its
        # toolchain's own output); consumed by that gate's own check only.
        self._gate_output_roots: Dict[str, set] = {}

    def _record(self, relpath: str) -> None:
        path = os.path.join(self.root, relpath)
        self._expected[relpath] = (_stat_identity(path), _content_identity(path))

    @property
    def paths(self) -> Tuple[str, ...]:
        return tuple(sorted(self._expected))

    def authorize(self, relpath: str) -> None:
        """Kriya itself just wrote ``relpath`` through the authorized writer."""
        self._record(relpath)

    def authorize_gate_output(self, gate: str, relroot: str) -> None:
        """The ``gate`` invocation now running runs a toolchain whose own
        output lands under ``relroot`` (e.g. its Maven invocation's
        ``target/``). Only that gate's own check accepts new files there; the
        files it legitimately created then belong to the baseline, so a later
        gate that does not run that toolchain cannot write there unseen."""
        relroot = relroot.strip("/")
        if relroot and relroot != "." and not relroot.startswith(".."):
            self._gate_output_roots.setdefault(gate, set()).add(relroot)

    def changes(self) -> List[Tuple[str, Optional[str], Optional[str]]]:
        changed = []
        for relpath, (identity, expected) in sorted(self._expected.items()):
            path = os.path.join(self.root, relpath)
            current_identity = _stat_identity(path)
            if current_identity == identity:
                continue
            actual = _content_identity(path)
            if actual != expected:
                changed.append((relpath, expected, actual))
            else:
                # Rewritten with identical bytes and mode (a touch): unchanged content.
                self._expected[relpath] = (current_identity, expected)
        return changed

    def check(self, gate: str, phase: str = "after", ephemeral_untracked: bool = False) -> List[str]:
        """``phase``: "after" the gate ran (the gate changed the tree) or
        "before" it (something between verification steps changed it).

        ``ephemeral_untracked`` (D6, the application-runtime gate only): the
        application under verification may create untracked state while it
        runs (work directories, logs, databases). That is execution state, not
        candidate source: it is removed here and returned (recorded) instead of
        stopping the run, so it never reaches a later gate, the commit, or
        recovery. A change to any bound (tracked or candidate) path is still
        the VerificationTreeMutated stop above; a file that cannot be removed
        fails closed."""
        changed = self.changes()
        if changed:
            raise VerificationTreeMutated(gate, changed, phase)
        roots = self._gate_output_roots.pop(gate, set()) if phase == "after" else set()
        new = untracked_repository_paths(self.root) - self._untracked_before - set(self._expected)
        owned = {path for path in new if any(path == root or path.startswith(root + "/") for root in roots)}
        created = sorted(new - owned)
        if created and ephemeral_untracked:
            self._discard(created)
            remaining = sorted(set(created) & untracked_repository_paths(self.root))
            if remaining:
                raise VerificationGateCreatedFiles(gate, remaining, phase)
        elif created:
            raise VerificationGateCreatedFiles(gate, created, phase)
        self._untracked_before |= owned
        return created

    def _discard(self, relpaths: Sequence[str]) -> None:
        """Remove runtime-created files, then any directory they leave empty."""
        root = os.path.realpath(self.root)
        for relpath in relpaths:
            path = os.path.join(root, relpath)
            try:
                os.unlink(path)
            except OSError:
                continue  # left in place: the caller's re-listing fails closed
            parent = os.path.dirname(path)
            while path_identity(root, parent) is not PathIdentity.SAME and path_relation(root, parent) is PathRelation.WITHIN:
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)

    def created(self) -> List[str]:
        """Untracked, non-ignored files that appeared since the binding (or
        since the last gate whose own output was accepted) and are neither
        candidate nor Kriya-authorized output."""
        return sorted(untracked_repository_paths(self.root) - self._untracked_before - set(self._expected))

"""REPOSITORY: the CURRENT PROJECT VIEW - a derived, bounded, read-only projection of what a project holds NOW (owner ruling 2026-11-07, Phase B).

It is a PROJECTION, never an authority. It promotes nothing: membership, ownership and read permission are decided by the owners it consumes, each consulted afresh on every
build (nothing is persisted, no second ledger):

    BOUND TREE   ``bound_tree.bound_tree_declaration``  -> which paths are visible/legal project paths, and Git's own tracked/untracked declaration
    JOURNAL      ``commit.list_commit_evidence``         -> which PATHS an earlier successful Kriya apply wrote (ownership-stamped, COMMITTED only; a later DELETE ends it)
    FILESYSTEM   ``os.lstat`` / the bytes now on disk    -> type, size, digest and CURRENT content (never the journal's historical bytes)
    READ POLICY  ``policy.filesystem.AuthorizedFileReader`` -> containment (symlinks resolved), hard links, secret-bearing names: a path it denies is not listed at all

Each visible path is one of:

    TRACKED              Git tracks it.
    KRIYA_ACCEPTED       Git does not track it, but a committed Kriya apply wrote this path (the file the run produced, before anyone ran ``git add``).
    AMBIGUOUS_UNTRACKED  Git does not track it and no Kriya apply wrote it: it exists, its origin is unknown. Only its NAME is ever shown - never its content - and being listed
                         gives it no write or apply authority, no candidate membership and no ownership (U2 is unchanged).

Content is shown only for a regular, non-symlink, UTF-8 text file of a TRACKED or KRIYA_ACCEPTED path, within a deterministic budget: every eligible file's content or none of them
(a large project keeps its bounded path inventory and its existing retrieval; nothing here selects content by a model or an embedding). A journal entry applies to the PATH: if the
path vanished it is simply absent, if it became a symlink or left the authorized roots it is shown without content or not at all - the journal never bypasses the path owners.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Iterable, Optional, Tuple

from kriya.policy.filesystem import AuthorizedFileReader
from kriya.repository.bound_tree import NO_GIT_REPOSITORY, bound_tree_declaration, workspace_root_class
from kriya.repository.commit import CommitState, list_commit_evidence

TRACKED = "TRACKED"
KRIYA_ACCEPTED = "KRIYA_ACCEPTED"
AMBIGUOUS_UNTRACKED = "AMBIGUOUS_UNTRACKED"

MAX_LISTED_PATHS = 200                 # the bounded inventory
MAX_CONTENT_FILES = 20                 # a "small project": at most this many content-eligible files ...
MAX_CONTENT_FILE_CHARS = 6_000         # ... each at most this large ...
MAX_CONTENT_TOTAL_CHARS = 24_000       # ... and at most this much text in total, else no file's content is shown

WITHHELD_AMBIGUOUS = "AMBIGUOUS_UNTRACKED"
WITHHELD_NOT_REGULAR = "NOT_A_REGULAR_FILE"
WITHHELD_TOO_LARGE = "TOO_LARGE"
WITHHELD_NOT_TEXT = "NOT_UTF8_TEXT"
WITHHELD_UNREADABLE = "UNREADABLE"
WITHHELD_PROJECT_TOO_LARGE = "PROJECT_TOO_LARGE_FOR_INLINE_CONTENT"


@dataclass(frozen=True)
class ProjectFile:
    path: str
    klass: str                       # TRACKED | KRIYA_ACCEPTED | AMBIGUOUS_UNTRACKED
    kriya_accepted: bool             # a committed Kriya apply wrote this path (independent of Git tracking)
    kind: str                        # "file" | "symlink" | "other"
    size: Optional[int]
    digest: Optional[str]            # sha256 of the bytes on disk NOW; None when the file is not content-eligible
    content: Optional[str]           # current text; None when withheld
    withheld: Optional[str]          # why no content is carried


@dataclass(frozen=True)
class ProjectView:
    files: Tuple[ProjectFile, ...]   # sorted by path, at most MAX_LISTED_PATHS
    omitted_paths: int               # visible paths beyond the bounded inventory
    contents_included: bool          # False: a large project - names only, contents stay with the existing retrieval

    @property
    def paths(self) -> FrozenSet[str]:
        return frozenset(entry.path for entry in self.files)

    def get(self, path: str) -> Optional[ProjectFile]:
        return next((entry for entry in self.files if entry.path == path), None)

    def counts(self) -> Dict[str, int]:
        counts = {TRACKED: 0, KRIYA_ACCEPTED: 0, AMBIGUOUS_UNTRACKED: 0}
        for entry in self.files:
            counts[entry.klass] += 1
        return counts


def accepted_paths(workspace_path: str) -> FrozenSet[str]:
    """The paths a committed Kriya apply wrote and no later committed apply deleted, from the existing commit journal (ownership-stamped to this workspace; an unreadable,
    foreign or non-COMMITTED document contributes nothing). Chronological by the journal's own clock; a path is accepted only while its newest committed operation is a write."""
    events = []
    for _path, evidence, error in list_commit_evidence(workspace_path):
        if error is not None or evidence is None or evidence.state is not CommitState.COMMITTED:
            continue
        events.append((evidence.updated_at_unix, evidence.transaction_id, evidence.operations))
    accepted = set()
    for _when, _transaction, operations in sorted(events, key=lambda item: (item[0], item[1])):
        for operation in operations:
            target = operation.get("target_path")
            if not isinstance(target, str) or not target:
                continue
            if operation.get("kind") == "DELETE":
                accepted.discard(target)
            elif operation.get("kind") in ("CREATE", "MODIFY"):
                accepted.add(target)
    return frozenset(accepted)


def _lstat_kind(full_path: str) -> Tuple[str, Optional[int]]:
    try:
        mode = os.lstat(full_path)
    except OSError:
        return "other", None
    if stat.S_ISLNK(mode.st_mode):
        return "symlink", None
    if stat.S_ISREG(mode.st_mode):
        return "file", mode.st_size
    return "other", None


def _read_text(full_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``(text, digest, withheld_reason)`` of a regular file, read without following a link swapped in after the lstat."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(full_path, flags)
    except OSError:
        return None, None, WITHHELD_UNREADABLE
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None, None, WITHHELD_NOT_REGULAR
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(MAX_CONTENT_FILE_CHARS * 4 + 1)
    except OSError:
        return None, None, WITHHELD_UNREADABLE
    finally:
        os.close(descriptor)
    if len(data) > MAX_CONTENT_FILE_CHARS * 4:
        return None, None, WITHHELD_TOO_LARGE
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, WITHHELD_NOT_TEXT
    if "\x00" in text:
        return None, None, WITHHELD_NOT_TEXT
    if len(text) > MAX_CONTENT_FILE_CHARS:
        return None, None, WITHHELD_TOO_LARGE
    return text, hashlib.sha256(data).hexdigest(), None


def _within_excluded_directory(workspace_path: str, path: str, excluded: Callable[[str], bool], memo: Dict[str, bool]) -> bool:
    parent = os.path.dirname(path)
    if parent == "":
        return False
    if parent not in memo:
        memo[parent] = excluded(os.path.join(workspace_path, parent)) or _within_excluded_directory(workspace_path, parent, excluded, memo)
    return memo[parent]


def build_current_project_view(workspace_path: str, *, exclude_directory: Optional[Callable[[str], bool]] = None) -> ProjectView:
    """The Current Project View of ``workspace_path`` as it is now. ``exclude_directory`` (absolute directory path -> bool) names Kriya's own knowledge directories (skills, memory, skill packages):
    nothing beneath one is project content, so nothing beneath one is listed - the caller supplies the same predicate the repository analyzer uses. Raises the bound tree's typed ``BoundTreeError`` when membership cannot be listed (a caller must not read that
    as "no files"); every other refusal is per path and visible in the entry (or the path is not listed: a secret-bearing or out-of-roots name is never exposed)."""
    if workspace_root_class(workspace_path) == NO_GIT_REPOSITORY:
        return ProjectView((), 0, True)      # a folder that is not a Git work tree reached a run only through U4's emptiness admission: it holds no project files yet
    tracked, untracked = bound_tree_declaration(workspace_path)
    accepted = accepted_paths(workspace_path)
    reader = AuthorizedFileReader(workspace_path)
    listed = []
    excluded_memo: Dict[str, bool] = {}
    for path in sorted(tracked | untracked):
        if exclude_directory is not None and _within_excluded_directory(workspace_path, path, exclude_directory, excluded_memo):
            continue
        full_path = os.path.join(workspace_path, path)
        if reader.authorize(full_path).decision != "allow":      # PolicyDecision.ALLOW (a str enum; policy.model is not a public contract module)
            continue                     # containment / hard-link / secret-bearing name: not even the name is shown
        is_tracked = path in tracked
        klass = TRACKED if is_tracked else KRIYA_ACCEPTED if path in accepted else AMBIGUOUS_UNTRACKED
        kind, size = _lstat_kind(full_path)
        listed.append((path, klass, path in accepted, kind, size, full_path))
    omitted = max(0, len(listed) - MAX_LISTED_PATHS)
    listed = listed[:MAX_LISTED_PATHS]

    candidates: Dict[str, Tuple[str, str]] = {}      # path -> (text, digest), only for content-eligible entries
    withheld: Dict[str, str] = {}
    for path, klass, _accepted, kind, _size, full_path in listed:
        if klass == AMBIGUOUS_UNTRACKED:
            withheld[path] = WITHHELD_AMBIGUOUS
        elif kind != "file":
            withheld[path] = WITHHELD_NOT_REGULAR
        else:
            text, digest, reason = _read_text(full_path)
            if reason is not None:
                withheld[path] = reason
            else:
                candidates[path] = (text or "", digest or "")
    contents_included = (
        len(candidates) <= MAX_CONTENT_FILES and sum(len(text) for text, _ in candidates.values()) <= MAX_CONTENT_TOTAL_CHARS
    )
    files = []
    for path, klass, was_accepted, kind, size, _full_path in listed:
        text_digest = candidates.get(path)
        if text_digest is not None and contents_included:
            files.append(ProjectFile(path, klass, was_accepted, kind, size, text_digest[1], text_digest[0], None))
        elif text_digest is not None:
            files.append(ProjectFile(path, klass, was_accepted, kind, size, text_digest[1], None, WITHHELD_PROJECT_TOO_LARGE))
        else:
            files.append(ProjectFile(path, klass, was_accepted, kind, size, None, None, withheld.get(path, WITHHELD_UNREADABLE)))
    return ProjectView(tuple(files), omitted, contents_included)


_CLASS_LABEL = {
    TRACKED: "tracked by Git",
    KRIYA_ACCEPTED: "created by an earlier Kriya run (not yet committed to Git)",
    AMBIGUOUS_UNTRACKED: "untracked, origin unknown - not created by Kriya",
}


def _render_inventory(view: ProjectView, *, with_content: bool, listed: int, omitted_note: str) -> str:
    entries = view.files[:listed]
    lines = ["=== Current Project View (what this project holds right now) ==="]
    hidden = view.omitted_paths + (len(view.files) - listed)
    lines.append(f"Project files ({len(entries)}{f' shown, {hidden} more not listed' if hidden else ''}):")
    for entry in entries:
        lines.append(f"- {entry.path} [{_CLASS_LABEL[entry.klass]}]")
    if any(entry.klass == AMBIGUOUS_UNTRACKED for entry in entries):
        lines.append("Files marked 'origin unknown' were not created by Kriya: their content is not shown and they are not part of this change unless the goal names them.")
    shown = [entry for entry in entries if entry.content is not None] if with_content else []
    if shown:
        lines.append("\nCurrent content (these are the files as they are NOW; a change to one of them MODIFIES it, it does not create a replacement):")
        for entry in shown:
            lines.append(f"\n--- {entry.path} ---\n{entry.content}")
    elif omitted_note:
        lines.append(f"\n{omitted_note}")
    elif not view.contents_included:
        lines.append("\nThis project is too large to show inline; file content comes from retrieval.")
    return "\n".join(lines) + "\n"


def render_project_view(view: ProjectView, *, max_chars: Optional[int] = None) -> str:
    """The Planner/Architect rendering: a bounded path inventory with each file's provenance class and, for a small project, the CURRENT content of every eligible file. ``max_chars`` is the
    request's own room for it (a small served context window must never be made unsatisfiable by this section): content first, then the inventory alone, then as many names as fit.
    Reference data: the file text is repository-controlled, so it is fenced by the caller as untrusted context."""
    if not view.files:
        return ""
    full = _render_inventory(view, with_content=True, listed=len(view.files), omitted_note="")
    if max_chars is None or len(full) <= max_chars:
        return full
    note = "File content is not shown here: this request's context window is small; the Developer is given the current content of the files being changed."
    names = _render_inventory(view, with_content=False, listed=len(view.files), omitted_note=note)
    if len(names) <= max_chars:
        return names
    for listed in range(len(view.files) - 1, 0, -1):
        text = _render_inventory(view, with_content=False, listed=listed, omitted_note=note)
        if len(text) <= max_chars:
            return text
    return ""


BEFORE_CONTEXT_MAX_CHARS = 8_000        # review prompts carry a bounded BEFORE: a file whose content does not fit is named as EXISTING with its content withheld


def render_before_context(view: ProjectView, paths: Iterable[str], *, max_chars: int = BEFORE_CONTEXT_MAX_CHARS) -> str:
    """The Reviewer/spec-compliance BEFORE rendering: what the project held before this candidate, bounded to ``max_chars`` of file content in total. The files the candidate touches come first
    (an existing one with its CURRENT content, a created one named NEW), then the project's other files - their current content while the budget lasts, then by name - because a goal that
    refers to "the app" can be judged only against the application that already exists, not against the candidate's own text. Empty when there is nothing to show."""
    touched = sorted(set(paths))
    touched_set = set(touched)
    budget = max_chars
    lines = []
    for path in touched:
        entry = view.get(path)
        if entry is None:
            lines.append(f"- {path}: NEW file (it does not exist in the project before this change)")
        elif entry.content is not None and len(entry.content) <= budget:
            budget -= len(entry.content)
            lines.append(f"- {path}: EXISTING file, its content before this change:\n{entry.content}")
        elif entry.content is not None:
            lines.append(f"- {path}: EXISTING file (its content before this change is too long to show here)")
        else:
            lines.append(f"- {path}: EXISTING file (content not shown: {entry.withheld})")
    shown, named = [], []
    for entry in view.files:
        if entry.path in touched_set:
            continue
        if entry.content is not None and len(entry.content) <= budget:
            budget -= len(entry.content)
            shown.append(f"- {entry.path} (not changed by this candidate), current content:\n{entry.content}")
        else:
            named.append(entry.path)
    if not lines and not shown and not named:
        return ""
    head = "=== Project BEFORE this change (reference data) ===\n"

    def build(body_lines, names):
        tail = f"\nOther files in the project (content not shown): {', '.join(names[:40])}{' ...' if len(names) > 40 else ''}" if names else ""
        return head + "\n".join(body_lines) + tail + "\n\n"

    text = build(lines + shown, named)
    if len(text) <= max_chars + len(head) + 400:      # content is bounded by max_chars; the headings and names around it get a fixed margin
        return text
    every_name = [entry.path for entry in view.files]
    names_only = build([f"- {path}: {'EXISTING' if view.get(path) else 'NEW'} file" for path in touched], [name for name in every_name if name not in touched_set])
    return names_only if len(names_only) <= max_chars + len(head) + 400 else ""


def project_view_event_details(view: ProjectView) -> Dict[str, object]:
    """The run-event details of a built view: counts only (never names or content)."""
    return {"counts": view.counts(), "listed": len(view.files), "omitted_paths": view.omitted_paths, "contents_included": view.contents_included,
            "content_files": sum(1 for entry in view.files if entry.content is not None)}

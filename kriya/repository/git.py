"""REPOSITORY: the repository-controlled content-filter refusal (S19 Q1, OI-141; owner decision 2026-10-20).

A Git content-filter driver (``filter.<name>.clean`` / ``smudge`` / ``process``) is a command Git runs ON THIS HOST
whenever it converts a file bound to ``filter=<name>`` by ``.gitattributes`` / ``.git/info/attributes``: ``status``,
``diff``, ``add`` and ``commit`` (clean, ``process``), ``checkout``, ``worktree add`` and ``archive`` (smudge) - 19 of
Kriya's own call sites (MEASURED, handover/MODULARIZATION/S19-PREFLIGHT.md section 2). Neutralising the drivers would
change Kriya's content/evidence hashes and the candidate bytes, so the owner decided: REFUSAL, never blanking.

``refuse_repository_content_filters(path)`` raises :class:`RepositoryContentFilterRefused` when an executable driver
command (any non-empty value, or a valueless key) ORIGINATES from repository-controlled configuration of the
repository Git would use at ``path``, or of any submodule / embedded repository Git recurses into from there:

* scope ``local`` (``.git/config``) or ``worktree`` (``config.worktree``) - including every file those scopes include
  through ``include.path`` / ``includeIf``, which Git reports with the INCLUDING file's scope (MEASURED, git 2.54.0);
* scope ``system`` / ``global`` whose origin FILE lies inside the repository (its work tree, git dir or common dir): an
  operator file that includes a repository file (fail-closed choice; owner question in the S19 Q1 report);
* any other scope (fail closed: only the operator scopes and command scope are trusted), and a trusted scope whose
  origin is not the expected shape (``GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED``).

Trusted (never refused): ``system`` and ``global`` definitions from files outside the repository (a user's Git LFS
``filter.lfs.*`` keeps working, and a repository may merely REFERENCE such a filter through ``.gitattributes``), and
``command`` scope (``-c`` / ``GIT_CONFIG_PARAMETERS`` / ``GIT_CONFIG_COUNT``: Kriya's own process environment and argv,
operator-controlled; Kriya itself never passes a filter command). Benign filter metadata (``filter.<x>.required``, any
other key) and an explicitly EMPTY command (Git runs nothing for it, MEASURED) are not executable and never refuse. The
ORIGIN decides, never the effective value: a repository that overrides a trusted global filter is refused.

Why every repository-scoped definition, bound or not: whether a path is bound to the filter is decided by
``.gitattributes``, which is repository CONTENT (model-writable) and can change between two Git calls; refusing the
definition itself is the one decision that does not depend on content (fail closed).

The inspection runs nothing a repository defines: ``git config --show-scope --show-origin -z --get-regexp`` reads
configuration only (MEASURED: no planted driver fires), and every spawn goes through ``kriya.platform.git_argv``
(fsmonitor / hooks off). Submodules: a parent ``status`` / ``diff`` / ``add`` runs the child Git of every populated
gitlink with the CHILD's own configuration (MEASURED, also for a gitlink without ``.gitmodules`` and for a gitlink
only HEAD still has, which the scratch-index hashes read), so the gitlinks of the index and of HEAD are inspected
recursively. Nothing is memoized: a repository's configuration (or a file it includes from the work tree) can change
between two calls, so every caller inspects immediately before its own Git operation.

The refusal message names the key, its scope and its origin file, never the command (it could carry a secret), and
goes through the one redactor (kriya.foundation.redaction). Callers: the run entry (``begin_mutating_run``) and every
Kriya function whose Git operation can run a filter (S19Q1-DECISION-TO-KILLER.md).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional, Set, Tuple

from kriya.foundation.redaction import redact_text
from kriya.platform import git_argv

GIT_CONTENT_FILTER_REPOSITORY_DEFINED = "GIT_CONTENT_FILTER_REPOSITORY_DEFINED"
GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY = "GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY"
GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED = "GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED"
GIT_CONFIG_UNINSPECTABLE = "GIT_CONFIG_UNINSPECTABLE"

_REMEDY = ("Define the filter in your global or system git configuration (for Git LFS: `git lfs install`, not "
           "`git lfs install --local`) and remove it from the repository's configuration.")
# The closed refusal table: reason code -> operator message (the command value never appears).
CONTENT_FILTER_REFUSALS: Dict[str, str] = {
    GIT_CONTENT_FILTER_REPOSITORY_DEFINED: (
        "the git repository at {repository} defines the content-filter command {key} in its own configuration "
        "({scope} scope, {origin}). Kriya does not run git there: a repository-controlled clean/smudge/process driver "
        "would execute on this host (a current security limitation, S19 Q1). " + _REMEDY),
    GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY: (
        "the content-filter command {key} for the git repository at {repository} is defined in {origin}, a file inside "
        "that repository, included by the {scope} git configuration. Kriya does not run git there: the file is "
        "repository content (a current security limitation, S19 Q1). " + _REMEDY),
    GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED: (
        "the content-filter command {key} for the git repository at {repository} comes from an unrecognised "
        "configuration origin ({scope} scope, {origin}). Kriya does not run git there (S19 Q1). " + _REMEDY),
    GIT_CONFIG_UNINSPECTABLE: (
        "the git configuration of {repository} could not be inspected for content-filter drivers ({detail}). Kriya "
        "does not run git there (S19 Q1)."),
}
# The closed origin tables: the operator scopes are trusted for files outside the repository, command scope for
# Kriya's own argv / environment; EVERY other scope is the repository's own (MEASURED: .git/config, config.worktree and
# everything they include report scope local or the per-worktree scope) and refuses. A trusted scope with an origin
# outside these shapes refuses as unrecognised.
OPERATOR_FILE_SCOPES = frozenset({"system", "global"})
COMMAND_SCOPE = "command"
_FILE_ORIGIN = "file:"
_COMMAND_ORIGIN = "command line:"
_FILTER_COMMAND_PATTERN = r"^filter\..*\.(clean|smudge|process)$"
_GITLINK_MODE = b"160000"
# Submodules nest; deeper than this is refused rather than half inspected.
_MAX_NESTING = 16
_TIMEOUT_SECONDS = 60


class RepositoryContentFilterRefused(Exception):
    """A typed refusal (S19 Q1): Kriya runs no git where a repository-controlled content filter could execute.
    ``str(error)`` is ``"<REASON_CODE>: <message>"`` (redacted). Deliberately not a RuntimeError/OSError/ValueError
    subclass, so no handler written for an ordinary git failure absorbs it."""

    def __init__(self, reason_code: str, repository: str, *, key: str = "", scope: str = "", origin: str = "",
                 detail: str = "") -> None:
        self.reason_code = reason_code
        self.repository = repository
        self.key = key
        self.scope = scope
        self.origin = origin
        message = CONTENT_FILTER_REFUSALS[reason_code].format(repository=repository, key=key, scope=scope,
                                                              origin=origin, detail=detail)
        super().__init__(redact_text(f"{reason_code}: {message}"))


def _run(cwd: str, *args: str) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(git_argv(["git", *args]), cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
                          timeout=_TIMEOUT_SECONDS, check=False)


def _uninspectable(repository: str, what: str, completed: "Optional[subprocess.CompletedProcess[bytes]]" = None) -> RepositoryContentFilterRefused:
    stderr = os.fsdecode(completed.stderr).strip()[:300] if completed is not None else ""
    detail = f"{what}" + (f": exit {completed.returncode}" if completed is not None else "") + (f", {stderr}" if stderr else "")
    return RepositoryContentFilterRefused(GIT_CONFIG_UNINSPECTABLE, repository, detail=detail)


def _filter_definitions(path: str) -> List[Tuple[str, str, str, Optional[str]]]:
    """(scope, origin, key, value) of every content-filter command Git sees at ``path``; value None = valueless key.
    ``-z``: ``scope NUL origin NUL key [LF value] NUL`` (MEASURED; the scope field is NUL-terminated, never TAB)."""
    done = _run(path, "config", "--show-scope", "--show-origin", "-z", "--get-regexp", _FILTER_COMMAND_PATTERN)
    if done.returncode == 1 and not done.stdout:       # no such key anywhere
        return []
    if done.returncode != 0:
        raise _uninspectable(path, "git config failed", done)
    fields = done.stdout.split(b"\0")
    if fields[-1] != b"" or (len(fields) - 1) % 3:
        raise _uninspectable(path, "unparseable git config listing")
    definitions = []
    for index in range(0, len(fields) - 1, 3):
        key, newline, value = fields[index + 2].partition(b"\n")
        definitions.append((os.fsdecode(fields[index]), os.fsdecode(fields[index + 1]), os.fsdecode(key),
                            os.fsdecode(value) if newline else None))
    return definitions


def _layout(path: str) -> Optional[Tuple[str, str, str]]:
    """(work tree top level, git dir, common dir) as absolute real paths; None when ``path`` is not in a work tree."""
    done = _run(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
    if done.returncode != 0:
        return None
    lines = os.fsdecode(done.stdout).split("\n")
    if len(lines) != 4 or lines[3] != "" or not all(lines[:3]):
        raise _uninspectable(path, "unparseable git rev-parse answer")
    top, git_dir, common = (os.path.realpath(line) for line in lines[:3])
    return top, git_dir, common


def _inside(path: str, roots: Tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


def _gitlinks(top: str) -> Set[str]:
    """Paths (relative to ``top``) of every gitlink in the index and in HEAD (a repository without a commit has none
    in HEAD)."""
    index = _run(top, "ls-files", "-s", "-z", "--full-name")
    if index.returncode != 0:
        raise _uninspectable(top, "git ls-files failed", index)
    records = [record for record in index.stdout.split(b"\0") if record]
    head = _run(top, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    if head.returncode == 0:
        records += [record for record in head.stdout.split(b"\0") if record]
    elif _run(top, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode == 0:
        raise _uninspectable(top, "git ls-tree HEAD failed", head)
    links = set()
    for record in records:
        meta, tab, name = record.partition(b"\t")
        if not tab:
            raise _uninspectable(top, "unparseable git listing")
        if meta.split(b" ", 1)[0] == _GITLINK_MODE:
            links.add(os.fsdecode(name))
    return links


def _refuse(path: str, seen: Set[str], depth: int) -> None:
    layout = _layout(path)
    for scope, origin, key, value in _filter_definitions(path):
        if value == "":
            continue                                    # an empty command: Git runs nothing (MEASURED)
        if scope not in OPERATOR_FILE_SCOPES and scope != COMMAND_SCOPE:
            raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, path, key=key, scope=scope,
                                                 origin=origin)
        if scope == COMMAND_SCOPE and origin == _COMMAND_ORIGIN:
            continue                                    # Kriya's own argv / process environment
        if scope in OPERATOR_FILE_SCOPES and origin.startswith(_FILE_ORIGIN):
            source = os.path.realpath(os.path.join(layout[0] if layout else path, origin[len(_FILE_ORIGIN):]))
            if layout is not None and _inside(source, layout):
                raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY, path, key=key,
                                                     scope=scope, origin=origin)
            continue
        raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED, path, key=key, scope=scope,
                                             origin=origin)
    if layout is None or layout[0] in seen:
        return
    seen.add(layout[0])
    for link in sorted(_gitlinks(layout[0])):
        child = os.path.join(layout[0], link)
        if not os.path.lexists(os.path.join(child, ".git")):
            continue                                    # not populated: Git does not recurse into it
        if depth >= _MAX_NESTING:
            raise _uninspectable(child, f"submodules nested deeper than {_MAX_NESTING}")
        _refuse(child, seen, depth + 1)


def refuse_repository_content_filters(path: str) -> None:
    """Raise RepositoryContentFilterRefused when Git at ``path`` (or in a submodule it recurses into) could run a
    content-filter command from repository-controlled configuration; return when it cannot. Call it immediately
    before a Git operation that can run a filter (status, diff, add, commit, checkout, worktree add, archive), on the
    folder that operation runs in. Returns without inspecting only when no Git can run there at all (``path`` is not
    a directory, or no ``git`` on PATH): then no filter can run either."""
    folder = os.fspath(path)
    if not os.path.isdir(folder) or shutil.which("git") is None:
        return
    try:
        _refuse(folder, set(), 0)
    except subprocess.TimeoutExpired as error:
        raise _uninspectable(folder, f"git did not answer within {_TIMEOUT_SECONDS} s") from error
    except OSError as error:
        raise _uninspectable(folder, f"git could not be run ({type(error).__name__})") from error

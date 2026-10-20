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
* scope ``system`` / ``global`` whose origin FILE lies inside the repository (any of its work trees - the main one and
  every linked one, S19 P1b -, its git dir or common dir): an operator file that includes a repository file, directly,
  transitively, conditionally (``includeIf``), relatively or through a symlink - the ULTIMATE file decides (owner
  decision C; MEASURED shapes in tests/test_s19p1b_residuals.py);
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
import re
import shutil
import subprocess
from typing import Callable, Dict, List, Mapping, Optional, Set, Tuple

from kriya.config.environment import child_environment
from kriya.foundation.redaction import redact_text
from kriya.platform import git_argv
from kriya.platform.filesystem_semantics import PathRelation, path_relation

GIT_CONTENT_FILTER_REPOSITORY_DEFINED = "GIT_CONTENT_FILTER_REPOSITORY_DEFINED"
GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY = "GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY"
GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED = "GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED"
GIT_CONFIG_UNINSPECTABLE = "GIT_CONFIG_UNINSPECTABLE"
GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN = "GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN"

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
    GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN: (
        "the content-filter command {key} for the git repository at {repository} ({scope} scope, {origin}) is not "
        "provably operator-controlled code: {detail}. Kriya does not run git there: git would run it on this host "
        "with the repository as its working directory (S19 P1c). Point the filter at an operator-installed program "
        "outside the repository, invoked directly (for Git LFS: `git lfs install`)."),
}
# The closed provenance causes (S19 P1c): the refusal names one of these, never the command text.
PROVENANCE_CAUSES: Dict[str, str] = {
    "structure": "it uses a shell construct or a character outside the conservative direct-command form (quotes, "
                 "expansions, substitutions, redirections, pipelines, separators, globbing, an assignment or a "
                 "second line)",
    "relative_program": "its program is a path relative to the repository",
    "program_in_repository": "its program resolves inside the repository",
    "evaluator": "its program is a shell, interpreter or evaluator ({name}) that would run program text Kriya "
                 "cannot prove operator-controlled",
    "argument_path": "an argument names a path relative to or inside the repository",
    "search_path": "the program search path has a relative, empty or in-repository entry before the program",
    "unresolved": "its program does not resolve to an executable on git's program search path",
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


_Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


def _runner(executable: Optional[str], env: Optional[Mapping[str, str]], timeout: float = _TIMEOUT_SECONDS) -> _Runner:
    """The git the inspection runs: the CALLER's own (S19 P1b, owner decision D). The inspection must read the very
    configuration the caller's git operation will read - another executable or environment can see another global or
    system file (``GIT_CONFIG_GLOBAL``, ``HOME``, ``GIT_CONFIG_NOSYSTEM``) or even another repository (``GIT_DIR``).
    Neither given = ``git`` from PATH in this process's environment (``_run``, looked up at call time), which is how
    every Kriya git call except the GUI's sanitized Open Folder probe spawns. ``timeout``: the CALLER's own per-spawn
    timeout (S19 P1c, owner decision 6: the Open Folder probe's 15 s, never widened to this module's 60 s)."""
    if executable is None and env is None and timeout == _TIMEOUT_SECONDS:
        return lambda cwd, *args: _run(cwd, *args)
    git = executable or "git"                           # a git literal the S19 seam rule sees
    child_env = None if env is None else dict(env)

    def run(cwd: str, *args: str) -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(git_argv([git, *args]), cwd=cwd, env=child_env, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    return run


# Git's repository-SELECTING environment (S19 P1c, finding 8(c)): it names the repository and index of the CALLER's
# operation. Git itself clears it before it runs the git of a submodule, and a question about ANOTHER folder (does
# this operator file / program lie in a work tree of this repository?) must not inherit it either: MEASURED, with
# GIT_DIR=<repository>/.git in the caller's environment, `rev-parse --git-common-dir` in an operator folder answered
# the repository's own common dir and a legitimate operator include was refused as in-repository. The ceiling is
# dropped too, which can only widen discovery (more folders count as the repository: fail closed). Nothing about
# ownership changes (safe.directory and the caller's config parameters are kept).
_REPOSITORY_SELECTING_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
                             "GIT_CEILING_DIRECTORIES")


class _Caller:
    """How the CALLER's git will run: ``run`` = its git and environment as given; ``elsewhere`` = the same git and
    environment without the repository-selecting variables (another folder's repository, a submodule's git);
    ``environ`` = the environment its git resolves a filter program with."""

    def __init__(self, executable: Optional[str], env: Optional[Mapping[str, str]], timeout: float) -> None:
        self.environ: Mapping[str, str] = child_environment() if env is None else env
        self.run = _runner(executable, env, timeout)
        if any(name in self.environ for name in _REPOSITORY_SELECTING_ENV):
            stripped = {k: v for k, v in self.environ.items() if k not in _REPOSITORY_SELECTING_ENV}
            self.elsewhere = _runner(executable, stripped, timeout)
        else:
            self.elsewhere = self.run
        self.exec_path: Optional[str] = None


def _uninspectable(repository: str, what: str, completed: "Optional[subprocess.CompletedProcess[bytes]]" = None) -> RepositoryContentFilterRefused:
    stderr = os.fsdecode(completed.stderr).strip()[:300] if completed is not None else ""
    detail = f"{what}" + (f": exit {completed.returncode}" if completed is not None else "") + (f", {stderr}" if stderr else "")
    return RepositoryContentFilterRefused(GIT_CONFIG_UNINSPECTABLE, repository, detail=detail)


def _filter_definitions(run: _Runner, path: str) -> List[Tuple[str, str, str, Optional[str]]]:
    """(scope, origin, key, value) of every content-filter command Git sees at ``path``; value None = valueless key.
    ``-z``: ``scope NUL origin NUL key [LF value] NUL`` (MEASURED; the scope field is NUL-terminated, never TAB)."""
    done = run(path, "config", "--show-scope", "--show-origin", "-z", "--get-regexp", _FILTER_COMMAND_PATTERN)
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


def _layout(run: _Runner, path: str) -> Optional[Tuple[str, str, str]]:
    """(work tree top level, git dir, common dir) as absolute real paths; None when ``path`` is not in a work tree."""
    done = run(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
    if done.returncode != 0:
        return None
    lines = os.fsdecode(done.stdout).split("\n")
    if len(lines) != 4 or lines[3] != "" or not all(lines[:3]):
        raise _uninspectable(path, "unparseable git rev-parse answer")
    top, git_dir, common = (os.path.realpath(line) for line in lines[:3])
    return top, git_dir, common


def _same_repository(run: _Runner, source: str, common: str) -> bool:
    """True when the folder holding ``source`` belongs to the repository whose common dir is ``common`` - any of its
    work trees, the main one or a linked one (S19 P1b, owner decision C - MEASURED: inspected from a linked worktree
    (Kriya's managed worktree is one) a global include of a MAIN work-tree file was admitted and its filter ran;
    inspected from the main work tree, a global include of a file in a linked worktree outside it likewise). Git
    answers the question itself: ``rev-parse --git-common-dir`` in that folder (reads no content: MEASURED, runs no
    filter). Only git's plain "not a git repository" answer means another place; any other failure refuses."""
    folder = os.path.dirname(source)
    done = run(folder, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if done.returncode != 0:
        if b"not a git repository" in done.stderr.lower():
            return False
        raise _uninspectable(folder, "git rev-parse of an operator configuration's folder failed", done)
    lines = os.fsdecode(done.stdout).split("\n")
    if len(lines) != 2 or lines[1] != "" or not lines[0]:
        raise _uninspectable(folder, "unparseable git rev-parse answer")
    return os.path.realpath(lines[0]) == common


def _inside(path: str, roots: Tuple[str, ...]) -> bool:
    """``path`` is one of ``roots`` or beneath it, by filesystem identity (kriya.platform's path_relation: case,
    normalization and symlink aliases); an undecidable relation counts as inside (fail closed)."""
    return any(path_relation(root, path) is not PathRelation.OUTSIDE for root in roots)


def _gitlinks(run: _Runner, top: str) -> Set[str]:
    """Paths (relative to ``top``) of every gitlink in the index and in HEAD (a repository without a commit has none
    in HEAD)."""
    index = run(top, "ls-files", "-s", "-z", "--full-name")
    if index.returncode != 0:
        raise _uninspectable(top, "git ls-files failed", index)
    records = [record for record in index.stdout.split(b"\0") if record]
    head = run(top, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    if head.returncode == 0:
        records += [record for record in head.stdout.split(b"\0") if record]
    elif run(top, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode == 0:
        raise _uninspectable(top, "git ls-tree HEAD failed", head)
    links = set()
    for record in records:
        meta, tab, name = record.partition(b"\t")
        if not tab:
            raise _uninspectable(top, "unparseable git listing")
        if meta.split(b" ", 1)[0] == _GITLINK_MODE:
            links.add(os.fsdecode(name))
    return links


# ----------------------------------------------------------------------------- executable-code provenance (S19 P1c)
#
# An operator (global/system) or command-scope definition is trusted by ORIGIN only when the code it runs is the
# operator's too (owner decision 2026-10-20: MEASURED, `clean = ./tools/clean.sh`, `sh tools/clean.sh`, `python3
# ./tools/filter.py`, an absolute or symlinked path into the work tree all ran repository code through a global
# definition). Git runs a filter command with the work tree's top as its working directory, through `/bin/sh -c` as
# soon as it holds a space or a shell character, else directly (MEASURED, git 2.54.0). This is NOT a shell parser: a
# command is admitted only in one conservative form, and anything outside it refuses (fail closed):
#
# * words separated by spaces/tabs, every word made only of _SHELL_WORD characters (no quote, $, `, \, ;, &, |, <,
#   >, (, ), *, ?, [, ], {, }, ~, #, !, newline): nothing is expanded, substituted, redirected, piped or globbed;
#   the first word holds no `=` (an assignment). Git's own `%f` (the quoted path it substitutes) is such a word.
# * the program (first word): a path with a `/` must be absolute and resolve - every ancestor and the file itself,
#   symlinks followed - outside every work tree, git dir and common dir of the repository; a bare name is resolved
#   exactly as git resolves it: `git --exec-path` first, then the caller's PATH entries in order (MEASURED both
#   orders, git 2.54.0); every entry up to the hit must be absolute and outside the repository (an empty or relative
#   entry is the work tree top: MEASURED, the program ran from there), and no hit = unresolved.
# * the program is not a shell, interpreter or evaluator (_EVALUATORS, matched on the written and the resolved name,
#   version suffix dropped): their program text is an argument or the repository itself, which Kriya does not
#   analyse. `git` is one (MEASURED: `git <alias>` ran a repository-local `alias.<x> = !./tools/clean.sh`).
# * no argument names a path relative to the work tree (`.`, `..`, or any word with a `/` not starting with `/`), and
#   an absolute argument resolves outside the repository like the program.
#
# No program name is ever trusted for its name (git-lfs is admitted because /path/to/git-lfs is outside the
# repository and called directly; a repository file called git-lfs is refused like any other repository path).

_SHELL_WORD = re.compile(r"[A-Za-z0-9_@%+=:,./-]+")
_WORD_SEPARATORS = re.compile(r"[ \t]+")
_VERSION_SUFFIX = re.compile(r"[0-9.]+$")
_EVALUATORS = frozenset({
    # shells and shell builtins / wrappers that run another command or program text
    "sh", "bash", "dash", "zsh", "ksh", "mksh", "ash", "csh", "tcsh", "fish", "busybox", "rbash", "pwsh", "powershell",
    ".", "source", "eval", "exec", "command", "builtin", "trap", "env", "xargs", "nohup", "nice", "timeout", "time",
    "stdbuf", "setsid", "sudo", "doas", "su", "chroot", "script", "watch", "parallel", "flock", "caffeinate",
    # interpreters and build/task runners that read program text from an argument or the working directory
    "python", "pythonw", "pypy", "perl", "ruby", "irb", "node", "nodejs", "deno", "bun", "php", "lua", "luajit",
    "tclsh", "wish", "expect", "osascript", "java", "jshell", "groovy", "scala", "kotlin", "rscript", "r", "julia",
    "erl", "escript", "elixir", "guile", "racket", "sbcl", "ocaml", "swift", "dotnet", "ts-node", "tsx", "awk",
    "gawk", "mawk", "nawk", "sed", "gsed", "ed", "ex", "vi", "vim", "nvim", "emacs", "make", "gmake", "cmake",
    "ninja", "rake", "npm", "npx", "yarn", "pnpm", "uv", "uvx", "pip", "pipx", "poetry", "cargo", "go", "gradle",
    "gradlew", "mvn", "mvnw", "ant", "bundle", "git",
})


def _unproven(path: str, key: str, scope: str, origin: str, cause: str, name: str = "") -> RepositoryContentFilterRefused:
    return RepositoryContentFilterRefused(GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN, path, key=key, scope=scope,
                                          origin=origin, detail=PROVENANCE_CAUSES[cause].format(name=name))


def _evaluator(program: str) -> Optional[str]:
    """The closed-set evaluator name ``program`` is, else None (the basename, lower case, version suffix dropped)."""
    base = os.path.basename(program).lower()
    for name in (base, _VERSION_SUFFIX.sub("", base)):
        if name in _EVALUATORS:
            return name
    return None


def _ancestry_in(candidate: str, roots: Tuple[str, ...]) -> bool:
    """True when ``candidate`` (absolute) or any of its ancestors, each resolved with symlinks followed, is one of
    ``roots`` or beneath one: a symlinked directory the repository controls counts, wherever it points."""
    current = os.path.normpath(candidate)
    while True:
        if _inside(os.path.realpath(current), roots):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _repository_code(caller: _Caller, candidate: str, roots: Tuple[str, ...], common: Optional[str]) -> bool:
    """``candidate`` (absolute) is repository content: inside a root by any spelling, or its real file lies in ANY
    work tree of the repository (git's own answer, as for an operator configuration file - S19 P1b C)."""
    if _ancestry_in(candidate, roots):
        return True
    real = os.path.realpath(candidate)
    return common is not None and os.path.isdir(os.path.dirname(real)) and _same_repository(caller.elsewhere, real, common)


def _resolve_program(caller: _Caller, path: str, name: str, roots: Tuple[str, ...]) -> Tuple[Optional[str], str]:
    """(the file git would run for the bare ``name``, "") or (None, a PROVENANCE_CAUSES key)."""
    if caller.exec_path is None:
        done = caller.run(path, "--exec-path")
        if done.returncode != 0:
            raise _uninspectable(path, "git --exec-path failed", done)
        caller.exec_path = os.fsdecode(done.stdout).strip()
    search_path = caller.environ.get("PATH")
    if search_path is None:
        return None, "unresolved"
    for entry in [caller.exec_path, *search_path.split(os.pathsep)]:
        if not entry or not os.path.isabs(entry) or _ancestry_in(entry, roots):
            return None, "search_path"
        candidate = os.path.join(entry, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate, ""
    return None, "unresolved"


def _provenance_cause(caller: _Caller, path: str, command: str, roots: Tuple[str, ...],
                      common: Optional[str]) -> Tuple[str, str]:
    """("", "") when ``command`` provably runs operator code; else (PROVENANCE_CAUSES key, evaluator name)."""
    words = [word for word in _WORD_SEPARATORS.split(command) if word]
    if not words or not all(_SHELL_WORD.fullmatch(word) for word in words) or "=" in words[0]:
        return "structure", ""
    program, arguments = words[0], words[1:]
    for argument in arguments:
        if argument in (".", "..") or ("/" in argument and (not os.path.isabs(argument)
                                                            or _repository_code(caller, argument, roots, common))):
            return "argument_path", ""
    name = _evaluator(program)
    if name:
        return "evaluator", name
    if "/" in program:
        if not os.path.isabs(program):
            return "relative_program", ""
        resolved: Optional[str] = program
    else:
        resolved, cause = _resolve_program(caller, path, program, roots)
        if resolved is None:
            return cause, ""
    name = _evaluator(os.path.realpath(resolved))
    if name:
        return "evaluator", name
    if _repository_code(caller, resolved, roots, common):
        return "program_in_repository", ""
    if not (os.path.isfile(resolved) and os.access(resolved, os.X_OK)):
        return "unresolved", ""
    return "", ""


def _refuse(caller: _Caller, run: _Runner, path: str, seen: Set[str], depth: int) -> None:
    layout = _layout(run, path)
    roots = layout if layout is not None else (os.path.realpath(path),)
    common = layout[2] if layout is not None else None
    for scope, origin, key, value in _filter_definitions(run, path):
        if value == "":
            continue                                    # an empty command: Git runs nothing (MEASURED)
        if scope not in OPERATOR_FILE_SCOPES and scope != COMMAND_SCOPE:
            raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, path, key=key, scope=scope,
                                                 origin=origin)
        if scope in OPERATOR_FILE_SCOPES and origin.startswith(_FILE_ORIGIN):
            source = os.path.realpath(os.path.join(layout[0] if layout else path, origin[len(_FILE_ORIGIN):]))
            if layout is not None and (_inside(source, layout) or _same_repository(caller.elsewhere, source, layout[2])):
                raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY, path, key=key,
                                                     scope=scope, origin=origin)
        elif not (scope == COMMAND_SCOPE and origin == _COMMAND_ORIGIN):    # command scope: Kriya's own argv / env
            raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED, path, key=key, scope=scope,
                                                 origin=origin)
        # A trusted ORIGIN: its executable code must be the operator's too (S19 P1c, owner decisions 1-3).
        cause, name = _provenance_cause(caller, path, value or "", roots, common)
        if cause:
            raise _unproven(path, key, scope, origin, cause, name)
    if layout is None or layout[0] in seen:
        return
    seen.add(layout[0])
    for link in sorted(_gitlinks(run, layout[0])):
        child = os.path.join(layout[0], link)
        if not os.path.lexists(os.path.join(child, ".git")):
            continue                                    # not populated: Git does not recurse into it
        if depth >= _MAX_NESTING:
            raise _uninspectable(child, f"submodules nested deeper than {_MAX_NESTING}")
        # A submodule's git runs without the caller's repository-selecting variables (git clears them), so its
        # configuration is read that way too (S19 P1c: a scratch GIT_INDEX_FILE names the PARENT's index only).
        _refuse(caller, caller.elsewhere, child, seen, depth + 1)


def refuse_repository_content_filters(path: str, *, git: Optional[str] = None,
                                      env: Optional[Mapping[str, str]] = None,
                                      timeout: float = _TIMEOUT_SECONDS) -> None:
    """Raise RepositoryContentFilterRefused when Git at ``path`` (or in a submodule it recurses into) could run a
    content-filter command from repository-controlled configuration, or an operator/command-scope command that is
    not provably operator code (S19 P1c); return when it cannot. Call it immediately before a Git operation that can
    run a filter (status, diff, add, commit, checkout, reset, archive), on the folder that operation runs in and with
    the environment it runs with - including a scratch ``GIT_INDEX_FILE``, whose gitlinks are the submodules that
    operation recurses into. ``git`` / ``env``: the executable and the environment THAT operation runs with, when
    they are not this process's PATH ``git`` and environment (the GUI's Open Folder probe, S19 P1b). ``timeout``: the
    caller's own per-spawn git timeout (the Open Folder probe's, S19 P1c). Returns without inspecting only when no Git
    can run there at all (``path`` is not a directory, or no such git): then no filter can run either."""
    folder = os.fspath(path)
    if not os.path.isdir(folder):
        return
    if git is None:
        if shutil.which("git", path=None if env is None else env.get("PATH", "")) is None:
            return
    elif not (os.path.isfile(git) and os.access(git, os.X_OK)):
        return
    caller = _Caller(git, env, timeout)
    try:
        _refuse(caller, caller.run, folder, set(), 0)
    except subprocess.TimeoutExpired as error:
        raise _uninspectable(folder, f"git did not answer within {timeout} s") from error
    except OSError as error:
        raise _uninspectable(folder, f"git could not be run ({type(error).__name__})") from error

"""CERT-GIT-MAINTENANCE-CLASS-001: the mechanical tripwire for "a test-created repository is observed while git's detached auto-maintenance may still be running".

THE DEFECT CLASS (MEASURED: hosted runs 41785193323, 41812207261, 41840628981; git 2.56.0): ``git commit`` (and every command that calls ``run_auto_maintenance``) starts
``git maintenance run --auto --quiet --detach``. For a moment that child holds ``.git/objects/maintenance.lock`` and then moves the mtime of ``.git/objects``. A test that
SNAPSHOTS the repository (lists ``.git`` or ``.git/objects``, hashes every file, compares mtimes) once right after a fixture commit and once later therefore sees a
difference no Kriya write caused. Three rounds of by-hand sweeping each missed a helper, so the population is derived here, from what actually runs, and never from a list.

WHAT IT RECORDS (CPython audit events, installed once at import, process-wide; no ``os.stat`` wrapper: ``shutil`` compares ``os.stat`` by identity)
  * UNGUARDED COMMIT: a ``subprocess.Popen`` of ``git`` whose subcommand is one that runs auto-maintenance and whose argv/env carries no ``maintenance.auto=false`` guard, in
    a directory inside a repository. Keyed by the repository's real path, with the innermost ``tests/`` frame (the helper) and whether any ``kriya/`` frame launched it.
  * OBSERVATION: ``os.scandir`` / ``os.listdir`` of ``<repo>/.git`` or ``<repo>/.git/objects`` - the only places the lock and the mtime move. (A walk that prunes ``.git`` never
    lists it and is correctly not an observer.)
WHAT IT DECIDES: a test that observed a repository that had an unguarded commit FAILS at teardown, naming the helper and the observer. ``KRIYA_GIT_QUIESCENCE=report:FILE``
appends one JSON record per overlap instead (the census mode). A repository whose unguarded commit came from PRODUCT code (a ``kriya/`` frame) is reported as such and is a
separate decision - it is the product's behaviour, not fixture setup.

Known, documented residual: an observer that never lists those two directories from Python (a bare ``os.stat`` of ``.git/objects``, an external ``tar``/``rsync`` process)."""
from __future__ import annotations

import json
import os
import re
import sys
import sysconfig
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: git subcommands that end in ``run_auto_maintenance`` (git source: builtin/commit.c, merge.c, fetch.c, am.c, rebase.c, receive-pack.c; ``pull`` runs fetch + merge/rebase).
AUTO_MAINTENANCE_SUBCOMMANDS = frozenset({"commit", "merge", "fetch", "pull", "am", "rebase", "receive-pack"})
_OPTIONS_WITH_A_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env"})
GUARD = "maintenance.auto=false"
ENV_MODE = "KRIYA_GIT_QUIESCENCE"
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_ARGUMENT = r'(?:[^\s"\']|"[^"]*"|\'[^\']*\')+'
#: a commit-class git as it is written in SHELL TEXT: `git`, any global options, then the subcommand IN THAT POSITION (not `merge-base`, not words in a message).
COMMIT_CLASS = re.compile(rf"\bgit\b(?:\s+(?:-[cC]\s+{_ARGUMENT}|--[\w-]+(?:={_ARGUMENT})?))*\s+(?:commit|merge|fetch|pull|am|rebase)(?![\w-])")
_CD = re.compile(r"(?:^|[;&|(\n])\s*cd\s+(" + _ARGUMENT + r")")
_SEGMENT_END = re.compile(r"&&|\|\||[;|\n)]")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PRODUCT_DIR = os.path.join(os.path.dirname(_TESTS_DIR), "kriya") + os.sep
_SELF = os.path.abspath(__file__)
def _library_roots() -> List[str]:
    roots = [os.path.dirname(os.__file__)]
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        found = sysconfig.get_paths().get(key)
        if found:
            roots.append(found)
    return roots


_LIBRARY_ROOTS = _library_roots()
_LIBRARY_DIRS = tuple(sorted({variant + os.sep for path in _LIBRARY_ROOTS for variant in (os.path.abspath(path), os.path.realpath(path))}))


def _is_library(filename: str) -> bool:
    return filename.startswith("<") or filename.startswith(_LIBRARY_DIRS)


@dataclass(frozen=True)
class UnguardedCommit:
    repository: str          # real path of the work tree
    subcommand: str
    helper: str              # innermost tests/ frame: file:function:line
    product: bool            # a kriya/ frame launched it


@dataclass(frozen=True)
class Violation:
    test: str
    repository: str
    commit: UnguardedCommit
    observer: str            # event + path + innermost tests//kriya/ frame
    listings: int = 1        # how many times this test listed that repository's .git / .git/objects (>= 2: a before/after snapshot pair)

    def render(self) -> str:
        origin = "PRODUCT code (a kriya/ frame launched it - report separately, do not guard the fixture)" if self.commit.product else "fixture/test code"
        return (f"git auto-maintenance can race this test's repository snapshot: {self.repository}\n"
                f"  unguarded `git {self.commit.subcommand}` from {origin}: {self.commit.helper}\n"
                f"  observed (listing .git or .git/objects) at: {self.observer}\n"
                f"  guard the fixture's git commit with `-c {GUARD} -c gc.auto=0` (tests/_named_test_oracle_harness.py::make_base is the model), "
                f"or prove this test never compares repository state after the commit.")


_commits: Dict[str, UnguardedCommit] = {}
_seen: Dict[Tuple[str, bool], UnguardedCommit] = {}     # (repository, guarded) -> first commit-class git there: the census of EVERY test-created repository that commits
_reported_seen: set = set()
_observations: List[Tuple[str, str, str]] = []     # (path, event, where) made during the current test
_current_test: List[str] = [""]


def subcommand_of(argv: Sequence[str]) -> Optional[str]:
    """The git subcommand of ``argv`` (global options such as ``-c k=v`` / ``-C dir`` skipped), or None."""
    tokens = list(argv)[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _OPTIONS_WITH_A_VALUE:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def is_guarded(argv: Sequence[str], env: Optional[dict]) -> bool:
    """The maintenance guard is on the command line (``-c maintenance.auto=false``) or in the command-scope environment the process was given."""
    if any(GUARD == str(token) for token in argv):
        return True
    environment = os.environ if env is None else env
    parameters = str(environment.get("GIT_CONFIG_PARAMETERS", "")).replace("'", "")
    if GUARD in parameters or "maintenance.auto=false" in parameters.replace("'", ""):      # git writes `'maintenance.auto'='false'`
        return True
    try:
        count = int(environment.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        count = 0
    for index in range(count):
        if environment.get(f"GIT_CONFIG_KEY_{index}") == "maintenance.auto" and str(environment.get(f"GIT_CONFIG_VALUE_{index}", "")).lower() in {"false", "0", "no", "off"}:
            return True
    return False


def _directory_of(argv: Sequence[str], cwd: object) -> str:
    base = os.fspath(cwd) if cwd else os.getcwd()
    tokens = list(argv)[1:]
    for index, token in enumerate(tokens[:-1]):
        if token == "-C":
            base = os.path.join(base, tokens[index + 1])
    return os.path.abspath(base)


def _work_tree_of(directory: str) -> Optional[str]:
    current = directory
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return os.path.realpath(current)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _short(filename: str) -> str:
    root = os.path.dirname(_TESTS_DIR)
    return os.path.relpath(filename, root) if filename.startswith(root + os.sep) else filename


def _frames() -> Tuple[str, bool]:
    """(innermost NON-LIBRARY frame as file:function:line - the helper, wherever it lives; whether any kriya/ frame is on the stack)."""
    helper, product = "", False
    frame = sys._getframe(2)
    while frame is not None:
        raw = frame.f_code.co_filename
        name = os.path.abspath(raw)
        if name.startswith(_PRODUCT_DIR):
            product = True
        elif not helper and name != _SELF and not _is_library(raw) and not _is_library(name):     # raw first: ``<frozen os>`` must not become <cwd>/<frozen os>
            helper = f"{_short(name)}:{frame.f_code.co_name}:{frame.f_lineno}"
        frame = frame.f_back
    return helper or "<no non-library frame>", product


def _observer_frame() -> str:
    frame = sys._getframe(2)
    while frame is not None:
        raw = frame.f_code.co_filename
        name = os.path.abspath(raw)
        if name != _SELF and not _is_library(raw) and not _is_library(name):
            return f"{_short(name)}:{frame.f_code.co_name}:{frame.f_lineno}"
        frame = frame.f_back
    return "<no non-library frame>"


def _text(value: object) -> Optional[str]:
    try:
        return os.fsdecode(value) if isinstance(value, (str, bytes, os.PathLike)) else None
    except (TypeError, ValueError):
        return None


def _record(repository: Optional[str], guarded: bool, subcommand: str) -> None:
    if repository is None or (repository, guarded) in _seen:
        return
    helper, product = _frames()
    _seen[(repository, guarded)] = record = UnguardedCommit(repository, subcommand, helper, product)
    if not guarded:
        _commits.setdefault(repository, record)


def commit_class_in_text(text: str) -> List[Tuple[str, bool, Optional[str]]]:
    """Every commit-class git written in shell ``text``: (subcommand, guarded, ``-C`` directory or None). A command's guard is the one on ITS OWN segment."""
    found = []
    for match in COMMIT_CLASS.finditer(text):
        if text[:match.start()].count('"') % 2:
            continue                                   # inside a quoted message: prose, not a command
        tail = _SEGMENT_END.search(text, match.end())
        segment = text[match.start(): tail.start() if tail else len(text)]
        directory = re.search(r"-C\s+(" + _ARGUMENT + ")", match.group(0))
        changed = [m.group(1).strip("\"\'") for m in _CD.finditer(text[:match.start()])]       # `cd DIR && git ...`: the last cd before it
        where = directory.group(1).strip("\"\'") if directory else None
        found.append((match.group(0).split()[-1], GUARD in segment, os.path.join(changed[-1], where) if changed and where else (where or (changed[-1] if changed else None))))
    return found


def _on_launch(argv_or_text: object, cwd: object, env: Optional[dict]) -> None:
    """One process start: a git argv, a shell ``-c`` text, a ``shell=True`` string, ``os.system``, ``os.posix_spawn`` / ``os.exec*``."""
    base = os.fspath(cwd) if cwd else os.getcwd()
    environment_guarded = is_guarded([], env)
    if isinstance(argv_or_text, (list, tuple)) and argv_or_text:
        argv = [str(token) for token in argv_or_text]
        if os.path.basename(argv[0]) == "git":
            subcommand = subcommand_of(argv)
            if subcommand in AUTO_MAINTENANCE_SUBCOMMANDS:
                _record(_work_tree_of(_directory_of(argv, base)), is_guarded(argv, env), subcommand)
            return
        if os.path.basename(argv[0]) in SHELLS and "-c" in argv[1:-1]:
            argv_or_text = argv[argv.index("-c") + 1]
        else:
            return
    text = _text(argv_or_text)
    if not text:
        return
    for subcommand, guarded, directory in commit_class_in_text(text):
        where = os.path.abspath(os.path.join(base, directory)) if directory else os.path.abspath(base)
        _record(_work_tree_of(where), guarded or environment_guarded, subcommand)


def _on_popen(args: tuple) -> None:
    _on_launch(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None, args[3] if len(args) > 3 else None)


def _on_listing(event: str, args: tuple) -> None:
    path = args[0] if args else None
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    try:
        text = os.fsdecode(path)
    except (TypeError, ValueError):
        return
    if ".git" not in text:
        return
    text = text.rstrip("/\\")
    if not (text.endswith(os.sep + ".git") or text.endswith(os.sep + ".git" + os.sep + "objects") or text in (".git", os.path.join(".git", "objects"))):
        return
    _observations.append((os.path.abspath(text), event, _observer_frame()))


def _audit(event: str, args: tuple) -> None:
    try:
        if event == "subprocess.Popen":
            _on_popen(args)
        elif event == "os.system":
            _on_launch(args[0] if args else None, None, None)
        elif event in ("os.posix_spawn", "os.exec"):
            _on_launch(args[1] if len(args) > 1 else None, None, args[2] if len(args) > 2 else None)
        elif event in ("os.scandir", "os.listdir"):
            _on_listing(event, args)
    except Exception:  # noqa: BLE001 - the tripwire must never change a test's behaviour; its own defects are caught by tests/test_git_quiescence_tripwire.py
        return


_installed = False


def install() -> None:
    global _installed  # noqa: PLW0603 - once per process, audit hooks cannot be removed
    if not _installed:
        sys.addaudithook(_audit)
        _installed = True


def begin_test(nodeid: str) -> None:
    _current_test[0] = nodeid
    _observations.clear()


def violations_of_current_test() -> List[Violation]:
    found: List[Violation] = []
    first: Dict[str, str] = {}
    listings: Dict[str, int] = {}
    for path, event, where in _observations:
        git_dir = path[: -len(os.sep + "objects")] if path.endswith(os.sep + "objects") else path
        work_tree = os.path.realpath(os.path.dirname(git_dir)) if os.path.basename(git_dir) == ".git" else ""
        if work_tree not in _commits:
            continue
        listings[work_tree] = listings.get(work_tree, 0) + 1
        first.setdefault(work_tree, f"{event}({path}) at {where}")
    for work_tree, observer in first.items():
        found.append(Violation(_current_test[0], work_tree, _commits[work_tree], observer, listings[work_tree]))
    return found


PRODUCT_MARKER = "git_quiescence_product_commit"


def judge(violations: Sequence[Violation], product_marker: bool) -> List[str]:
    """The failure messages for a test's violations. A violation whose unguarded commit came from PRODUCT code is not a fixture defect (the owner decides what the product's git
    does); a test that reads no ``.git`` bytes may acknowledge it with ``@pytest.mark.git_quiescence_product_commit``. The acknowledgement is checked both ways: it never excuses a
    fixture-origin overlap, and a marker with no product-origin overlap behind it is itself a failure (a stale exemption would be a hand-kept list)."""
    fixture = [v for v in violations if not v.commit.product]
    product = [v for v in violations if v.commit.product]
    messages = [v.render() for v in fixture]
    if product and not product_marker:
        messages.extend(v.render() for v in product)
    if product_marker and not product:
        messages.append(f"@pytest.mark.{PRODUCT_MARKER} is stale: no repository this test listed had an unguarded commit launched by product (kriya/) code - remove the marker")
    return messages


def forget_everything() -> None:
    """For the tripwire's own tests: start from an empty record."""
    _commits.clear()
    _seen.clear()
    _reported_seen.clear()
    _observations.clear()


def new_commit_records(test: str) -> List[dict]:
    """Report mode: every commit-class git seen since the last call, guarded or not (the census of test-created repositories that commit)."""
    fresh = []
    for key, commit in list(_seen.items()):
        if key not in _reported_seen:
            _reported_seen.add(key)
            fresh.append({"kind": "commit", "test": test, "repository": commit.repository, "guarded": key[1], "subcommand": commit.subcommand, "helper": commit.helper, "product": commit.product})
    return fresh


def report(violations: Sequence[Violation], destination: str, commits: Sequence[dict] = ()) -> None:
    with open(destination, "a", encoding="utf-8") as handle:
        for record in commits:
            handle.write(json.dumps(record) + "\n")
        for violation in violations:
            handle.write(json.dumps({"kind": "overlap", "test": violation.test, "repository": violation.repository, "subcommand": violation.commit.subcommand,
                                     "helper": violation.commit.helper, "product": violation.commit.product, "observer": violation.observer,
                                     "listings": violation.listings}) + "\n")


def mode() -> str:
    return os.environ.get(ENV_MODE, "enforce")

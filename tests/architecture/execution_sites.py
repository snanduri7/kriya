"""The scanner behind the execution-site census (D2 EXEC-LAYER rebuild, slice S1).

Finds every function in production code (``kriya/``, ``plugins/``) that launches an external process, by AST (code only, never prose
or docstrings). A launch is one of:

- ``subprocess.run/Popen/check_output/check_call/call/getoutput/getstatusoutput`` (also the bare names imported from ``subprocess``);
- ``asyncio.create_subprocess_exec/shell``;
- ``os.system/popen/exec*/spawn*/posix_spawn*/fork*`` and anything on ``pty``; ``multiprocessing.Process/Pool``;
- the two Kriya spawn helpers ``_spawn_popen`` and ``spawn_subprocess_exec_fail_closed`` (``kriya/tools/process.py``);
- ``<x>.run / run_async / start_managed(..., cwd=...)`` in a module that imports ``ProcessController`` (the receiver is a variable, a
  parameter, a call result or ``self._process``: matching on the receiver NAME misses real launches, which a first draft did; the
  ``cwd=`` keyword plus the import is the ProcessController signature).

A launch whose first positional argument is a ``git_argv(...)`` call is a GIT launch: counted per file, because the git seam has its own
structural test (``test_git_invocation_seam``) and its own measured evidence.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, NamedTuple, Set

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("kriya", "plugins")

_SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call", "getoutput", "getstatusoutput"}
_ASYNC = {"create_subprocess_exec", "create_subprocess_shell"}
_OS = {"system", "popen", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle",
       "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp", "fork", "forkpty"}
_CONTROLLER_METHODS = {"run", "run_async", "start_managed"}
_HELPERS = {"_spawn_popen", "spawn_subprocess_exec_fail_closed"}


class Launch(NamedTuple):
    kind: str
    line: int
    git: bool


def _qualname(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> str:
    names: List[str] = []
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return ".".join(reversed(names)) or "<module>"


def _is_git_argv(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "git_argv")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "git_argv"))


def _imports_controller(tree: ast.Module) -> bool:
    """The module can reach ``ProcessController``: it imports the name, the module ``kriya.tools.process`` (any spelling) or ``process``
    from ``kriya.tools``. A module that imports none of these cannot call the controller's methods."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "kriya.tools.process" or any(alias.name == "ProcessController" for alias in node.names):
                return True
            if node.module == "kriya.tools" and any(alias.name == "process" for alias in node.names):
                return True
        if isinstance(node, ast.Import) and any(alias.name == "kriya.tools.process" for alias in node.names):
            return True
    return False


def launches(source: str) -> Dict[str, List[Launch]]:
    """Every function that launches a process -> its launches."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    from_subprocess: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            from_subprocess |= {alias.asname or alias.name for alias in node.names}
    controller_module = _imports_controller(tree)
    found: Dict[str, List[Launch]] = defaultdict(list)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func, kind = node.func, None
        if isinstance(func, ast.Attribute):
            owner = func.value.id if isinstance(func.value, ast.Name) else None
            if owner == "subprocess" and func.attr in _SUBPROCESS:
                kind = f"subprocess.{func.attr}"
            elif func.attr in _ASYNC:
                kind = f"asyncio.{func.attr}"
            elif owner == "os" and func.attr in _OS:
                kind = f"os.{func.attr}"
            elif owner == "pty":
                kind = f"pty.{func.attr}"
            elif owner == "multiprocessing" and func.attr in ("Process", "Pool"):
                kind = f"multiprocessing.{func.attr}"
            elif controller_module and func.attr in _CONTROLLER_METHODS and any(kw.arg == "cwd" for kw in node.keywords):
                kind = f"ProcessController.{func.attr}"
            elif func.attr in _HELPERS:
                kind = f"helper.{func.attr}"
        elif isinstance(func, ast.Name):
            if func.id in from_subprocess and func.id in _SUBPROCESS:
                kind = f"subprocess.{func.id}"
            elif func.id in _HELPERS:
                kind = f"helper.{func.id}"
        if kind is not None:
            first = node.args[0] if node.args else None
            found[_qualname(parents, node)].append(Launch(kind, node.lineno, first is not None and _is_git_argv(first)))
    return found


def production_launches() -> Dict[str, Dict[str, List[Launch]]]:
    """relative file -> function -> launches, over all production code."""
    result: Dict[str, Dict[str, List[Launch]]] = {}
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            found = launches(path.read_text(encoding="utf-8"))
            if found:
                result[path.relative_to(REPO).as_posix()] = dict(found)
    return result

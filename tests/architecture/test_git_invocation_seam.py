"""Every git invocation Kriya spawns goes through ``kriya.platform.git_argv`` (S19, OI-141).

handover/MODULARIZATION/S19-PREFLIGHT.md MEASURED a repository's own configuration (core.fsmonitor, hooks, gpg.program)
executing on the host through 26 of Kriya's call sites, each of which built and spawned its own argv. The rule is
structural so a new call site cannot reintroduce it:

- a git argv literal - a list/tuple whose first element is the string ``"git"``, or a LIST whose first element is a
  name ``git`` (a resolved executable: ``shutil.which("git")``, a PATH candidate) - is either the argument of
  ``git_argv(...)`` or bound to a local name that the same function passes to ``git_argv(...)`` and never spawns raw;
- no ``create_subprocess_exec("git", ...)`` and no ``"git ..."`` command string;
- the only other git literals are the closed ``NON_SPAWN`` table (nothing spawned), each entry still present.

Applied after the policy audit at the spawn (the audited argv and the POL-001-P3 exact-prefix recognizers are
unchanged); ``test_direct_access_owners``' subprocess / git_argv counts are unchanged because the literal stays in place.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
SEAM = "git_argv"
SEAM_MODULE = "kriya/platform/__init__.py"
_SPAWNERS = ("run", "run_async", "start_managed", "Popen", "check_output", "check_call", "call",
             "create_subprocess_exec", "create_subprocess_shell", "_command")

# (relative file, enclosing function, literal source) -> why nothing is spawned
NON_SPAWN: Dict[Tuple[str, str, str], str] = {
    ("plugins/core_tools/__init__.py", "authorize_model_invocation", "('git', sub)"):
        "the PolicyDeniedError's ActionRequest names the refused planned commit; nothing is spawned",
}


def _python_files() -> List[str]:
    found = []
    for top in ("kriya", "plugins"):
        for root, dirs, names in os.walk(REPO / top):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            found.extend(os.path.relpath(os.path.join(root, n), REPO).replace(os.sep, "/") for n in names if n.endswith(".py"))
    return sorted(found)


def _is_git_literal(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return False
    first = node.elts[0]
    if isinstance(first, ast.Constant) and first.value == "git":
        return True
    return isinstance(node, ast.List) and isinstance(first, ast.Name) and first.id == "git"


def _callee(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _is_seam(call: ast.AST, argument: ast.AST) -> bool:
    return isinstance(call, ast.Call) and _callee(call) == SEAM and bool(call.args) and call.args[0] is argument


def violations(source: str, relpath: str) -> List[str]:
    """Every git argv in ``source`` that is spawned without the seam (and every stale NON_SPAWN entry it holds)."""
    tree = ast.parse(source)
    parents: Dict[ast.AST, ast.AST] = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def function_of(node: ast.AST) -> ast.AST:
        while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node = parents[node]
        return node

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if _callee(node) in _SPAWNERS and isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and (first.value == "git" or first.value.startswith("git ")):
                found.append(f"{relpath}:{node.lineno} spawns git from a string, outside {SEAM}()")
        if not _is_git_literal(node):
            continue
        parent = parents.get(node)
        if _is_seam(parent, node):
            continue
        scope = function_of(node)
        name = getattr(scope, "name", "<module>")
        if (relpath, name, ast.unparse(node)) in NON_SPAWN:
            continue
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            var = parent.targets[0].id
            calls = [c for c in ast.walk(scope) if isinstance(c, ast.Call) and c.args
                     and isinstance(c.args[0], ast.Name) and c.args[0].id == var]
            wrapped = [c for c in calls if _callee(c) == SEAM]
            raw = [c for c in calls if _callee(c) in _SPAWNERS]
            if wrapped and not raw:
                continue
            found.append(f"{relpath}:{node.lineno} git argv `{var}` is spawned without {SEAM}()")
            continue
        found.append(f"{relpath}:{node.lineno} git argv {ast.unparse(node)[:60]} is not passed through {SEAM}()")
    return found


def _non_spawn_present(relpath: str, source: str) -> set:
    tree = ast.parse(source)
    present = set()
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                if _is_git_literal(node):
                    present.add((relpath, function.name, ast.unparse(node)))
    return present


def test_every_kriya_git_spawn_goes_through_the_seam():
    found, present = [], set()
    for relpath in _python_files():
        if relpath == SEAM_MODULE:
            continue
        source = (REPO / relpath).read_text(encoding="utf-8")
        found += violations(source, relpath)
        present |= _non_spawn_present(relpath, source)
    assert found == []
    assert set(NON_SPAWN) <= present, f"stale NON_SPAWN entries: {sorted(set(NON_SPAWN) - present)}"


def test_the_seam_module_holds_no_git_literal_of_its_own():
    """PLATFORM is not a git_argv owner (test_direct_access_owners): the seam inserts options, it never names git."""
    tree = ast.parse((REPO / SEAM_MODULE).read_text(encoding="utf-8"))
    assert [n.lineno for n in ast.walk(tree) if _is_git_literal(n)] == []


def test_the_rule_catches_every_unwrapped_shape():
    """Planted controls: each forbidden shape is found, each wrapped shape passes."""
    planted = {
        "import subprocess\nsubprocess.run(['git', 'status'])\n": 1,
        "import subprocess\nsubprocess.run(git_argv(['git', 'status']))\n": 0,
        "import subprocess\ndef f():\n    cmd = ['git', 'status']\n    cmd += ['-z']\n    subprocess.run(cmd)\n": 1,
        "import subprocess\ndef f():\n    cmd = ['git', 'status']\n    subprocess.run(git_argv(cmd))\n": 0,
        "import subprocess\ndef f():\n    cmd = ['git', 'status']\n    subprocess.run(git_argv(cmd))\n    subprocess.run(cmd)\n": 1,
        "import subprocess, shutil\ngit = shutil.which('git')\nsubprocess.run([git, 'rev-parse'])\n": 1,
        "import subprocess, shutil\ngit = shutil.which('git')\nsubprocess.run(git_argv([git, 'rev-parse']))\n": 0,
        "import asyncio\nasyncio.create_subprocess_exec('git', 'log')\n": 1,
        "import subprocess\nsubprocess.run('git status', shell=True)\n": 1,
        "def f():\n    steps = (['git', 'read-tree'], ['git', 'write-tree'])\n": 2,
        "def f():\n    steps = (git_argv(['git', 'read-tree']), git_argv(['git', 'write-tree']))\n": 0,
        "def f():\n    ProcessController().run(['git', 'status'], cwd='.')\n": 1,
        "def f():\n    git, top = probe()\n    return (git, top, '')\n": 0,      # a result tuple, not an argv
    }
    for source, expected in planted.items():
        assert len(violations(source, "kriya/planted.py")) == expected, source

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

W3-2 (GIT-AUTOCRLF-HOST-CONFIG-CANDIDATE-BYTES-001) adds the ENVIRONMENT half of the same authority: every Kriya-owned git
execution consumes BOTH ``git_argv`` and ``git_environment``. The property is tested, not one syntax: the environment reaches the
process by whichever legitimate transport the spawn uses -

- ``env=`` of ``subprocess.run`` / ``Popen`` / a helper such as ``_command``, directly or through a local name bound to it (a
  caller-built mapping: ``{**git_environment(child_environment()), "GIT_INDEX_FILE": index}`` - policy first, the per-operation value
  AFTER, so policy never overwrites Kriya's own scratch index);
- ``containment_profile=`` whose ``ContainmentProfile(..., resolved_env=git_environment(...))`` is the existing containment contract
  (the ProcessController rejects ``env=`` together with a profile).

Forbidden: a git spawn that carries neither, and any module outside the seam naming a policy variable (a hand-rebuilt policy).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


# ---- W3-2: the environment half of the authority ----------------------------------------------------------------------------------

ENV_SEAM = "git_environment"
POLICY_VARIABLES = frozenset({"GIT_ATTR_NOSYSTEM", "GIT_ATTR_SOURCE", "GIT_LITERAL_PATHSPECS"})
_PROFILE = "ContainmentProfile"
_MAX_BINDING_DEPTH = 6


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Call) and _callee(n) == name for n in ast.walk(node))


def _bindings(scope: ast.AST) -> Dict[str, List[ast.AST]]:
    """The names bound DIRECTLY in ``scope``: a nested function / class body is another scope, so its names are not visible here (a
    sibling function's ``env`` or ``profile`` must never satisfy this scope's lookup - found by mutant C03)."""
    bound: Dict[str, List[ast.AST]] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        bound.setdefault(target.id, []).append(child.value)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name) and child.value is not None:
                bound.setdefault(child.target.id, []).append(child.value)
            visit(child)

    visit(scope)
    return bound


def _carries_policy(expression: ast.AST, bound: Dict[str, List[ast.AST]], depth: int = 0) -> bool:
    """The expression is (or is a local name bound to) a value built through ``git_environment``."""
    if _calls(expression, ENV_SEAM):
        return True
    if isinstance(expression, ast.Name) and depth < _MAX_BINDING_DEPTH:
        return any(_carries_policy(value, bound, depth + 1) for value in bound.get(expression.id, []))
    return False


def _profile_carries_policy(expression: ast.AST, bound: Dict[str, List[ast.AST]], depth: int = 0) -> bool:
    """A ``ContainmentProfile(...)`` (inline, or a local name bound to one) whose ``resolved_env`` carries the policy."""
    if isinstance(expression, ast.Call) and _callee(expression) == _PROFILE:
        return any(k.arg == "resolved_env" and _carries_policy(k.value, bound) for k in expression.keywords)
    if isinstance(expression, ast.Name) and depth < _MAX_BINDING_DEPTH:
        return any(_profile_carries_policy(value, bound, depth + 1) for value in bound.get(expression.id, []))
    return False


def environment_violations(source: str, relpath: str) -> List[str]:
    """Every git spawn in ``source`` whose execution does not carry the canonical environment, and every hand-rebuilt policy variable."""
    tree = ast.parse(source)
    parents: Dict[ast.AST, ast.AST] = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def scope_of(node: ast.AST) -> ast.AST:
        while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node = parents[node]
        return node

    def visible_bindings(scope: ast.AST) -> Dict[str, List[ast.AST]]:
        """The names a spawn in ``scope`` can see: its own and every enclosing function's / the module's (a closure's ``env``)."""
        merged: Dict[str, List[ast.AST]] = {}
        node: Optional[ast.AST] = scope
        while node is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                for name, values in _bindings(node).items():
                    merged.setdefault(name, []).extend(values)
            node = parents.get(node)
        return merged

    found: List[str] = []
    scopes = {scope_of(n) for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee(n) == SEAM}
    for scope in scopes:
        bound = visible_bindings(scope)
        for call in (n for n in ast.walk(scope) if isinstance(n, ast.Call) and _callee(n) in _SPAWNERS and n.args):
            argv = call.args[0]
            # a git execution: its argv is built by git_argv here, or is a name/loop variable of a scope that builds git argv
            if not (_calls(argv, SEAM) or isinstance(argv, ast.Name)):
                continue
            keywords = {k.arg: k.value for k in call.keywords if k.arg}
            if "env" in keywords and _carries_policy(keywords["env"], bound):
                continue
            if "containment_profile" in keywords and _profile_carries_policy(keywords["containment_profile"], bound):
                continue
            found.append(f"{relpath}:{call.lineno} git is spawned without the canonical environment ({ENV_SEAM}) - neither env= nor "
                         "a ContainmentProfile resolved_env carries it")
    if relpath != SEAM_MODULE:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in POLICY_VARIABLES:
                found.append(f"{relpath}:{node.lineno} names the git environment policy variable {node.value!r} outside the seam "
                             "(a hand-rebuilt policy)")
    return found


def test_every_kriya_git_execution_consumes_the_canonical_environment():
    found = []
    for relpath in _python_files():
        if relpath == SEAM_MODULE:
            continue
        found += environment_violations((REPO / relpath).read_text(encoding="utf-8"), relpath)
    assert found == []


def test_the_environment_rule_catches_every_shape_and_accepts_every_legitimate_transport():
    ok_subprocess = "import subprocess\ndef f():\n    subprocess.run(git_argv(['git', 'x']), env=git_environment(child_environment()))\n"
    planted = {
        # accepted transports
        ok_subprocess: 0,
        "import subprocess\ndef f():\n    env = {**git_environment(child_environment()), 'GIT_INDEX_FILE': i}\n"
        "    subprocess.run(git_argv(['git', 'x']), env=env)\n": 0,
        "def f():\n    profile = ContainmentProfile(trust_class=t, workspace_path=p, resolved_env=git_environment(child_environment()))\n"
        "    ProcessController().run(git_argv(['git', 'x']), containment_profile=profile, containment_backend=b)\n": 0,
        "def f():\n    ProcessController().run(git_argv(['git', 'x']), containment_profile=ContainmentProfile(\n"
        "        trust_class=t, workspace_path=p, resolved_env=git_environment(child_environment())))\n": 0,
        "import subprocess\ndef f():\n    steps = (git_argv(['git', 'a']), git_argv(['git', 'b']))\n"
        "    env = {**git_environment(child_environment()), 'GIT_INDEX_FILE': i}\n    for step in steps:\n        subprocess.run(step, env=env)\n": 0,
        "def f():\n    return _command(git_argv(['git', 'x']), cwd=c, env=git_environment(child_environment()))\n": 0,
        "import subprocess\ndef outer():\n    child = git_environment(child_environment())\n    def run():\n"
        "        return subprocess.run(git_argv(['git', 'x']), env=child)\n    return run\n": 0,        # a closure's name, bound outside
        "import subprocess\ndef outer():\n    child = child_environment()\n    def run():\n"
        "        return subprocess.run(git_argv(['git', 'x']), env=child)\n    return run\n": 1,
        # forbidden: a SIBLING function's wired name must not satisfy this function's lookup (mutant C03: module-wide lookup)
        "def wired():\n    profile = ContainmentProfile(trust_class=t, workspace_path=p, resolved_env=git_environment(child_environment()))\n"
        "    ProcessController().run(git_argv(['git', 'a']), containment_profile=profile, containment_backend=b)\n"
        "def unwired():\n    profile = ContainmentProfile(trust_class=t, workspace_path=p)\n"
        "    ProcessController().run(git_argv(['git', 'b']), containment_profile=profile, containment_backend=b)\n": 1,
        "import subprocess\ndef wired():\n    env = git_environment(child_environment())\n    subprocess.run(git_argv(['git', 'a']), env=env)\n"
        "def unwired():\n    env = child_environment()\n    subprocess.run(git_argv(['git', 'b']), env=env)\n": 1,
        # forbidden: no environment at all / the inherited one
        "import subprocess\ndef f():\n    subprocess.run(git_argv(['git', 'x']))\n": 1,
        "import subprocess\ndef f():\n    subprocess.run(git_argv(['git', 'x']), env=child_environment())\n": 1,
        "import subprocess\ndef f():\n    subprocess.run(git_argv(['git', 'x']), env=None)\n": 1,
        "import subprocess\ndef f():\n    env = {**child_environment(), 'GIT_INDEX_FILE': i}\n    subprocess.run(git_argv(['git', 'x']), env=env)\n": 1,
        # forbidden: a ProcessController profile that omits the policy (or carries an unrelated resolved_env), or no profile env at all
        "def f():\n    profile = ContainmentProfile(trust_class=t, workspace_path=p)\n"
        "    ProcessController().run(git_argv(['git', 'x']), containment_profile=profile, containment_backend=b)\n": 1,
        "def f():\n    profile = ContainmentProfile(trust_class=t, workspace_path=p, resolved_env=child_environment())\n"
        "    ProcessController().run(git_argv(['git', 'x']), containment_profile=profile, containment_backend=b)\n": 1,
        "def f(profile):\n    ProcessController().run(git_argv(['git', 'x']), containment_profile=profile, containment_backend=b)\n": 1,
        # forbidden: one spawn wired, the other not (a function-level check would pass this)
        "import subprocess\ndef f():\n    subprocess.run(git_argv(['git', 'a']), env=git_environment(child_environment()))\n"
        "    subprocess.run(git_argv(['git', 'b']))\n": 1,
        # forbidden: a hand-rebuilt policy, even where the spawn is otherwise wired
        ok_subprocess + "ENV = {'GIT_ATTR_NOSYSTEM': '1'}\n": 1,
        ok_subprocess + "def g(e):\n    e.pop('GIT_ATTR_SOURCE', None)\n": 1,
        ok_subprocess + "def g(e):\n    e['GIT_LITERAL_PATHSPECS'] = '0'\n": 1,
        # not git: untouched
        "import subprocess\ndef f():\n    subprocess.run(['docker', 'ps'])\n": 0,
    }
    for source, expected in planted.items():
        assert len(environment_violations(source, "kriya/planted.py")) == expected, source


def test_the_seam_module_owns_the_policy_and_the_policy_variables_are_closed():
    tree = ast.parse((REPO / SEAM_MODULE).read_text(encoding="utf-8"))
    names = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert POLICY_VARIABLES <= names, "the seam no longer names a policy variable this tripwire guards"
    from kriya.platform import GIT_FORCED_ENVIRONMENT, GIT_STRIPPED_ENVIRONMENT
    assert set(GIT_FORCED_ENVIRONMENT) | set(GIT_STRIPPED_ENVIRONMENT) == POLICY_VARIABLES

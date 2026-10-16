"""AST site finders for the direct-access and kernel-purity tripwires
(full modularization, slice A2; handover/MODULARIZATION/DEPENDENCY-RULES.md §2).

A "site" is one source line holding at least one matching AST node; a
module's count is its number of such lines. Unlike the Wave 1 line regex
behind CURRENT-DEPENDENCY-MAP.md §5 (which also counted prose, generated
source strings and type annotations), these finders match code only:
docstrings, comments and the text of string literals never count, except
for the finders that are about string literals (provider words, `.kriya`
paths), which skip docstrings. Every finder works on the session-cached
``module_map.trees()``.
"""
from __future__ import annotations

import ast
import collections
import re
from typing import Callable, Dict, FrozenSet, Iterator, List, Mapping, Set

from . import module_map as mm

Finder = Callable[[ast.Module], Set[int]]


class _Index:
    """Every node of one module bucketed by type, built in ONE walk and kept on
    the (session-cached) tree, so the finders never re-walk a module."""

    def __init__(self, tree: ast.Module) -> None:
        self.by_type: Dict[type, List[ast.AST]] = collections.defaultdict(list)
        self.in_functions: List[ast.AST] = []   # nodes inside a function body
        self.docstrings: Set[int] = set()
        stack = [(tree, False)]
        while stack:
            node, in_function = stack.pop()
            self.by_type[type(node)].append(node)
            if in_function:
                self.in_functions.append(node)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                        and isinstance(first.value.value, str):
                    self.docstrings.add(id(first.value))
            inner = in_function or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            stack.extend((child, inner) for child in ast.iter_child_nodes(node))

    def of(self, *types: type) -> Iterator[ast.AST]:
        for kind in types:
            yield from self.by_type.get(kind, ())


def _index(tree: ast.Module) -> _Index:
    index = getattr(tree, "_kriya_access_index", None)
    if index is None:
        index = _Index(tree)
        tree._kriya_access_index = index
    return index


def strings(tree: ast.Module) -> Iterator[ast.Constant]:
    index = _index(tree)
    for node in index.of(ast.Constant):
        if isinstance(node.value, str) and id(node) not in index.docstrings:
            yield node


def _attr_of(node: ast.AST, names: FrozenSet[str]) -> bool:
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names


def subprocess_import_sites(tree: ast.Module) -> Set[int]:
    """``import subprocess`` / ``from subprocess import``."""
    return {node.lineno for node in _index(tree).of(ast.Import, ast.ImportFrom)
            if (isinstance(node, ast.Import) and any(a.name.split(".")[0] == "subprocess" for a in node.names))
            or (isinstance(node, ast.ImportFrom) and node.level == 0
                and (node.module or "").split(".")[0] == "subprocess")}


def subprocess_sites(tree: ast.Module) -> Set[int]:
    """``subprocess.<x>``, ``from subprocess import``, ``Popen``,
    ``asyncio.create_subprocess_*``."""
    lines = set()
    for node in _index(tree).of(ast.Attribute, ast.Name, ast.ImportFrom):
        if _attr_of(node, frozenset({"subprocess"})) \
                or (isinstance(node, ast.Attribute) and node.attr.startswith("create_subprocess_")) \
                or (isinstance(node, ast.Name) and node.id == "Popen") \
                or (isinstance(node, ast.ImportFrom) and node.level == 0
                    and (node.module or "").split(".")[0] == "subprocess"):
            lines.add(node.lineno)
    return lines


_SPAWN_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call", "create_subprocess_exec",
                          "spawn_subprocess_exec_fail_closed"})


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def git_argv_sites(tree: ast.Module) -> Set[int]:
    """The literal ``"git"`` as argv[0]: first element of a list/tuple
    literal, or first positional argument of a spawn call."""
    lines = set()
    for node in _index(tree).of(ast.List, ast.Tuple, ast.Call):
        first = None
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
        elif isinstance(node, ast.Call) and node.args and _call_name(node) in _SPAWN_CALLS:
            first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == "git":
            lines.add(first.lineno)
    return lines


def _attr_or_import(tree: ast.Module, module: str, attrs: FrozenSet[str]) -> Set[int]:
    lines = set()
    for node in _index(tree).of(ast.Attribute, ast.ImportFrom):
        if _attr_of(node, frozenset({module})) and node.attr in attrs:
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == module \
                and any(alias.name in attrs for alias in node.names):
            lines.add(node.lineno)
    return lines


def environ_sites(tree: ast.Module) -> Set[int]:
    """``os.environ`` / ``os.environb`` / ``os.getenv`` (and their ``from os import``)."""
    return _attr_or_import(tree, "os", frozenset({"environ", "environb", "getenv"}))


def home_sites(tree: ast.Module) -> Set[int]:
    """``Path.home()`` and any ``expanduser`` (``os.path.expanduser``, ``Path.expanduser``)."""
    lines = set()
    for node in _index(tree).of(ast.Attribute):
        if isinstance(node, ast.Attribute) and (
                node.attr == "expanduser" or (node.attr == "home" and isinstance(node.value, ast.Name)
                                              and node.value.id == "Path")):
            lines.add(node.lineno)
    return lines


def cwd_sites(tree: ast.Module) -> Set[int]:
    """``os.getcwd()`` / ``os.getcwdb()`` / ``Path.cwd()``."""
    lines = _attr_or_import(tree, "os", frozenset({"getcwd", "getcwdb"}))
    for node in _index(tree).of(ast.Attribute):
        if isinstance(node, ast.Attribute) and node.attr == "cwd" and isinstance(node.value, ast.Name) \
                and node.value.id == "Path":
            lines.add(node.lineno)
    return lines


def sqlite_sites(tree: ast.Module) -> Set[int]:
    """``sqlite3.<x>`` and ``from sqlite3 import``."""
    lines = set()
    for node in _index(tree).of(ast.Attribute, ast.ImportFrom):
        if _attr_of(node, frozenset({"sqlite3"})) or (
                isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "sqlite3"):
            lines.add(node.lineno)
    return lines


_HTTP_MODULES = frozenset({"httpx", "requests", "aiohttp"})


def http_sites(tree: ast.Module) -> Set[int]:
    """``httpx``/``requests``/``aiohttp`` attribute use or ``from`` import, and
    ``urllib.request`` (attribute chain or import)."""
    imported = set()
    for node in _index(tree).of(ast.Import):
        if isinstance(node, ast.Import):
            imported |= {alias.asname or alias.name for alias in node.names if alias.name in _HTTP_MODULES}
    lines = set()
    for node in _index(tree).of(ast.Attribute, ast.ImportFrom, ast.Import):
        if (_attr_of(node, frozenset(imported))
                or (isinstance(node, ast.Attribute) and node.attr == "request" and _attr_of(node, frozenset({"urllib"})))
                or (isinstance(node, ast.ImportFrom) and node.level == 0
                    and ((node.module or "").split(".")[0] in _HTTP_MODULES or node.module == "urllib.request"
                         or (node.module == "urllib" and any(a.name == "request" for a in node.names))))
                or (isinstance(node, ast.Import) and any(a.name == "urllib.request" for a in node.names))):
            lines.add(node.lineno)
    return lines


PROVIDER_WORDS = re.compile(r"\b(ollama|vllm|openai)\b", re.IGNORECASE)
_PROVIDER_PACKAGES = frozenset({"ollama", "vllm", "openai"})


def provider_word_sites(tree: ast.Module) -> Set[int]:
    """A provider name in a (non-docstring) string literal, or an import of a
    provider SDK package."""
    lines = {node.lineno for node in strings(tree) if PROVIDER_WORDS.search(node.value)}
    for node in _index(tree).of(ast.Import, ast.ImportFrom):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] in _PROVIDER_PACKAGES for a in node.names):
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                and (node.module or "").split(".")[0] in _PROVIDER_PACKAGES:
            lines.add(node.lineno)
    return lines


_DIRECTORY_READS = frozenset({"listdir", "scandir", "walk", "iterdir", "glob", "iglob", "rglob"})


def skill_dir_read_sites(tree: ast.Module) -> Set[int]:
    """A directory listing (``os.listdir/scandir/walk``, ``glob``,
    ``Path.iterdir/glob/rglob``) whose call text names a skills path."""
    return {node.lineno for node in _index(tree).of(ast.Call)
            if isinstance(node, ast.Call) and _call_name(node) in _DIRECTORY_READS
            and "skill" in ast.unparse(node).lower()}


def tool_registry_sites(tree: ast.Module) -> Set[int]:
    """``<...>.registry.<method>("tool", ...)``: a read or write of the
    executing tool registry (``Kernel.registry`` category ``"tool"``)."""
    return {node.lineno for node in _index(tree).of(ast.Call)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "tool"
            and ast.unparse(node.func.value).endswith("registry")}


_KRIYA_DIR = re.compile(r"(^|[/\\])\.kriya([/\\]|$)")


def kriya_dir_sites(tree: ast.Module) -> Set[int]:
    """A (non-docstring) string literal that is, or contains a path
    component, ``.kriya``."""
    return {node.lineno for node in strings(tree) if _KRIYA_DIR.search(node.value)}


_DICT_FACTORIES = frozenset({"dict", "defaultdict", "OrderedDict", "WeakValueDictionary", "WeakKeyDictionary"})
_MUTATORS = frozenset({"setdefault", "update", "pop", "popitem", "clear"})


def mutated_module_dicts(tree: ast.Module) -> Set[str]:
    """Module-level dict names that a function body writes into: the shape of
    a registry (or cache) held in process-global state."""
    names = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] \
            if isinstance(node, ast.AnnAssign) and node.value is not None else []
        value = getattr(node, "value", None)
        if isinstance(value, (ast.Dict, ast.DictComp)) or (
                isinstance(value, ast.Call) and _call_name(value) in _DICT_FACTORIES):
            names |= {target.id for target in targets if isinstance(target, ast.Name)}
    mutated: Set[str] = set()
    if not names:
        return mutated
    for node in _index(tree).in_functions:
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            mutated |= {target.value.id for target in targets if isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name) and target.value.id in names}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _MUTATORS and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in names:
            mutated.add(node.func.value.id)
    return mutated


def counts(finder: Finder, trees: Mapping[str, ast.Module] = None) -> Dict[str, int]:
    """dotted module -> number of site lines, for every module with at least one."""
    found = {}
    for path, tree in (mm.trees() if trees is None else trees).items():
        lines = finder(tree)
        if lines:
            found[mm.module_name(path)] = len(lines)
    return found

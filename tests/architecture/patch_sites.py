"""Patch-site finder and lookup-resolution analysis for the kernel modules (full modularization E1).

A test patch ``patch("kriya.workflow.attempt.X")`` replaces the module attribute ``X``; it intercepts a call only
where the calling code looks ``X`` up THROUGH that module at call time: a bare-name load inside the module itself,
an attribute access ``attempt.X`` on the module object, or a function-local ``from kriya.workflow.attempt import X``
executed after the patch. A module-level ``from kriya.workflow.attempt import X`` elsewhere binds the original
object at import, so that module's calls are NOT intercepted. An E-slice that moves ``X``'s callers into a new module
(keeping ``X`` re-exported on the facade) therefore silently turns the patch vacuous even though the name still
exists; ``resolving_lookups`` measures exactly that.

Forms found in tests (AST, every ``tests/**/*.py``): ``patch("<dotted>")`` / ``mock.patch(...)`` /
``unittest.mock.patch(...)`` (call or decorator), ``patch.object(<module or class expr>, "<attr>")``,
``patch.multiple(<target>, name=...)``, ``patch.dict("<dotted>")``, ``monkeypatch.setattr("<dotted>", value)``,
``monkeypatch.setattr(<expr>, "<attr>", value)`` (any ``*.setattr``), ``monkeypatch.delattr`` likewise, and plain
assignment ``<module alias>.X = value``. ``<expr>`` resolves through the file's imports (any scope) and module-level
string constants; a string target is resolved from constants, f-strings and ``+`` concatenations of them. A target
that names a kernel module but cannot be resolved is reported as ``UNRESOLVED`` (never silently dropped).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Set, Tuple

KERNEL_MODULES = ("kriya.workflow.attempt", "kriya.workflow.workflow", "kriya.workflow.workflow_controller")
TESTS = Path(__file__).resolve().parent.parent
REPO = TESTS.parent
UNRESOLVED = "<unresolved>"


class PatchSite(NamedTuple):
    file: str        # path relative to the repository
    line: int
    module: str      # the kernel module whose attribute is patched
    target: str      # dotted path below the module ("run_attempt", "WorkflowEngine.run_generation_workflow")
    form: str        # patch | patch.object | patch.multiple | patch.dict | setattr | delattr | assign


def _aliases(tree: ast.AST) -> Dict[str, str]:
    """Local name -> dotted object path, from every import in the file (any scope) and module-level str constants."""
    names: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    names.setdefault(alias.name.split(".")[0], alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _constants(tree: ast.Module) -> Dict[str, str]:
    constants: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _string(node.value, constants)
            if value is not None and UNRESOLVED not in value:
                constants[node.targets[0].id] = value
    return constants


def _string(node: ast.AST, constants: Mapping[str, str]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, UNRESOLVED)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append(_string(value.value, constants) or UNRESOLVED)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string(node.left, constants), _string(node.right, constants)
        return None if left is None or right is None else left + right
    return None


def _dotted(node: ast.AST, aliases: Mapping[str, str]) -> Optional[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, aliases)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _split(dotted: Optional[str]) -> Optional[Tuple[str, str]]:
    if not dotted:
        return None
    for module in KERNEL_MODULES:
        if dotted.startswith(module + "."):
            return module, dotted[len(module) + 1:]
    return None


def _callee(node: ast.Call, aliases: Mapping[str, str]) -> str:
    """'patch' / 'patch.object' / 'patch.multiple' / 'patch.dict' / 'setattr' / 'delattr' / ''."""
    func = node.func
    dotted = _dotted(func, aliases) or ""
    last = dotted.rsplit(".", 1)[-1]
    if last == "patch" and (dotted in ("patch", "mock.patch", "unittest.mock.patch") or dotted.endswith(".patch")):
        return "patch"
    if isinstance(func, ast.Attribute) and func.attr in ("object", "multiple", "dict"):
        owner = _dotted(func.value, aliases) or ""
        if owner.rsplit(".", 1)[-1] == "patch":
            return f"patch.{func.attr}"
    if isinstance(func, ast.Attribute) and func.attr in ("setattr", "delattr") and isinstance(func.value, (ast.Name, ast.Attribute)):
        receiver = _dotted(func.value, aliases) or ""
        if receiver not in ("builtins",):
            return func.attr
    return ""


def sites_in_source(relpath: str, source: str) -> List[PatchSite]:
    tree = ast.parse(source)
    aliases, constants = _aliases(tree), _constants(tree)
    found: List[PatchSite] = []

    def add(node: ast.AST, dotted: Optional[str], form: str) -> None:
        if dotted is None:
            return
        if UNRESOLVED in dotted:
            if any(module.split(".")[-1] in dotted for module in KERNEL_MODULES) and "kriya.workflow" in dotted:
                found.append(PatchSite(relpath, node.lineno, UNRESOLVED, dotted, form))
            return
        split = _split(dotted)
        if split:
            found.append(PatchSite(relpath, node.lineno, split[0], split[1], form))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    base = _dotted(target.value, aliases)
                    if base in KERNEL_MODULES:
                        add(node, f"{base}.{target.attr}", "assign")
            continue
        if not isinstance(node, ast.Call):
            continue
        form = _callee(node, aliases)
        if not form or not node.args:
            continue
        first = node.args[0]
        if form in ("patch", "patch.dict"):
            add(node, _string(first, constants), form)
        elif form == "patch.object" and len(node.args) >= 2:
            attr = _string(node.args[1], constants)
            base = _dotted(first, aliases)
            if attr is not None and base is not None:
                add(node, f"{base}.{attr}", form)
        elif form == "patch.multiple":
            base = _string(first, constants) or _dotted(first, aliases)
            for keyword in node.keywords:
                if base is not None and keyword.arg:
                    add(node, f"{base}.{keyword.arg}", form)
        elif form in ("setattr", "delattr"):
            text = _string(first, constants)
            if text is not None and "." in text and (len(node.args) == (2 if form == "setattr" else 1)):
                add(node, text, form)          # monkeypatch.setattr("pkg.mod.name", value)
            elif len(node.args) >= 2:
                attr = _string(node.args[1], constants)
                base = _dotted(first, aliases)
                if attr is not None and base is not None:
                    add(node, f"{base}.{attr}", form)
    return found


def test_sources() -> Dict[str, str]:
    return {str(path.relative_to(REPO)): path.read_text(encoding="utf-8")
            for path in sorted(TESTS.rglob("*.py")) if "__pycache__" not in path.parts}


def patch_sites(sources: Optional[Mapping[str, str]] = None) -> List[PatchSite]:
    sources = test_sources() if sources is None else sources
    return [site for relpath, source in sources.items() for site in sites_in_source(relpath, source)]


def inventory(sites: Iterable[PatchSite]) -> Dict[Tuple[str, str], int]:
    counts: Dict[Tuple[str, str], int] = {}
    for site in sites:
        counts[(site.module, site.target)] = counts.get((site.module, site.target), 0) + 1
    return counts


# --------------------------------------------------------------------------- lookup resolution over kriya/

def _module_name(relpath: str) -> str:
    name = relpath[:-3].replace("/", ".")
    return name[:-len(".__init__")] if name.endswith(".__init__") else name


def _resolve_relative(module: str, node: ast.ImportFrom, is_package: bool) -> Optional[str]:
    if node.level == 0:
        return node.module
    base = module.split(".") if is_package else module.split(".")[:-1]
    if node.level > 1:
        base = base[:len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


class Lookups(NamedTuple):
    # (module, number of lookup sites) whose code looks the name up THROUGH the patched module at call time: bare-name
    # loads inside the module's own functions (a local variable of the same name is over-counted, never missed),
    # ``alias.name`` attribute loads, function-local ``from <module> import name``
    interceptable: Tuple[Tuple[str, int], ...]
    bound_at_import: Tuple[str, ...]  # modules that bound the original object at import: a patch never reaches them


class LookupIndex:
    """One pass over the kriya/ trees: for every (module, name) who resolves ``module.name`` and how."""

    def __init__(self, trees: Mapping[str, ast.Module]):
        self._interceptable: Dict[Tuple[str, str], Dict[str, int]] = {}
        self._bound: Dict[Tuple[str, str], Set[str]] = {}
        for relpath, tree in trees.items():
            if relpath.startswith("kriya/") and relpath.endswith(".py"):
                self._index(_module_name(relpath), tree, relpath.endswith("__init__.py"))

    def _add(self, table: Dict[Tuple[str, str], Any], module: str, name: str, here: str) -> None:
        entry = table.setdefault((module, name), {} if table is self._interceptable else set())
        if isinstance(entry, dict):
            entry[here] = entry.get(here, 0) + 1
        else:
            entry.add(here)

    def _index(self, here: str, tree: ast.Module, is_package: bool) -> None:
        top_level = {id(node) for node in tree.body}
        module_aliases: Dict[str, str] = {}          # local name -> a module object it is bound to
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        module_aliases[alias.asname] = alias.name
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_relative(here, node, is_package)
                if not resolved:
                    continue
                for alias in node.names:
                    # ``from pkg import mod``: the local name may be a module object
                    module_aliases.setdefault(alias.asname or alias.name, f"{resolved}.{alias.name}")
                    table = self._bound if id(node) in top_level else self._interceptable
                    self._add(table, resolved, alias.name, here)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id in module_aliases:
                    self._add(self._interceptable, module_aliases[node.value.id], node.attr, here)
                else:
                    dotted = _dotted(node.value, {})
                    if dotted and dotted.startswith("kriya."):
                        self._add(self._interceptable, dotted, node.attr, here)
        inside: Set[int] = set()
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                inside.update(id(node) for node in ast.walk(function)
                              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load))
        for node in ast.walk(tree):
            if id(node) in inside:   # each load once, however deeply its function is nested
                self._add(self._interceptable, here, node.id, here)

    def lookups(self, module: str, name: str) -> Lookups:
        return Lookups(tuple(sorted(self._interceptable.get((module, name), {}).items())),
                       tuple(sorted(self._bound.get((module, name), ()))))

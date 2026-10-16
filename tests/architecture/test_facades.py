"""Import facades (full modularization, slice A2; DEPENDENCY-RULES.md §4.3, §4.9).

A facade keeps an old import path alive while its symbols live in their
owner. A file listed in ``FACADES`` contains ONLY a module docstring that
names its removal slice and ``from <owner> import X as X`` lines from its
declared owner module; every re-exported name is the owner's object. Any
other statement fails. Every pure re-export module of kriya/ must be listed
(a facade cannot appear silently), and P1 empties ``FACADES`` (an entry may
only stay with a written justification, DEPENDENCY-RULES §4.9).
"""
from __future__ import annotations

import ast
import importlib
from typing import Dict, List, Mapping, NamedTuple

import pytest

from . import module_map as mm
from .test_import_direction import DONE_SLICES, SLICES


class Facade(NamedTuple):
    owner: str          # dotted owner module every line imports from
    removal_slice: str  # the slice that deletes the facade
    reason: str


FACADES: Dict[str, Facade] = {
    "kriya/build_info.py": Facade("kriya.foundation.build_info", "P1",
                                  "build identity moved to FOUNDATION in A1; old import path kept"),
    "kriya/core/tomlcompat.py": Facade("kriya.foundation.tomlcompat", "P1",
                                       "tomllib shim moved to FOUNDATION in A1; old import path kept"),
}


def _docstring(tree: ast.Module) -> str:
    return ast.get_docstring(tree) or ""


def facade_problems(path: str, tree: ast.Module, facade: Facade) -> List[str]:
    problems = []
    body = list(tree.body)
    if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        problems.append("no module docstring")
    else:
        body = body[1:]
    if facade.removal_slice not in _docstring(tree):
        problems.append(f"docstring does not name removal slice {facade.removal_slice}")
    if not body:
        problems.append("re-exports nothing")
    for node in body:
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or node.module != facade.owner:
            problems.append(f"line {node.lineno}: not `from {facade.owner} import X as X`")
            continue
        problems.extend(f"line {node.lineno}: {alias.name} is not re-exported as itself"
                        for alias in node.names if alias.asname != alias.name)
    return [f"{path}: {problem}" for problem in problems]


def pure_reexport_modules(trees: Mapping[str, ast.Module]) -> List[str]:
    found = []
    for path, tree in trees.items():
        body = [node for node in tree.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
        if body and all(isinstance(node, ast.ImportFrom) and all(a.asname == a.name for a in node.names)
                        for node in body):
            found.append(path)
    return sorted(found)


def test_the_a1_facades_are_listed():
    assert {"kriya/build_info.py", "kriya/core/tomlcompat.py"} <= set(FACADES)


@pytest.mark.parametrize("path", sorted(FACADES))
def test_a_facade_contains_only_reexports_from_its_owner(path):
    facade = FACADES[path]
    assert facade_problems(path, mm.trees()[path], facade) == []
    assert facade.removal_slice in SLICES and facade.removal_slice not in DONE_SLICES and facade.reason


@pytest.mark.parametrize("path", sorted(FACADES))
def test_every_reexported_name_is_the_owners_object(path):
    facade_module = importlib.import_module(mm.module_name(path))
    owner = importlib.import_module(FACADES[path].owner)
    names = [alias.name for node in mm.trees()[path].body if isinstance(node, ast.ImportFrom) for alias in node.names]
    assert names and all(getattr(facade_module, name) is getattr(owner, name) for name in names)


def test_no_unlisted_facade():
    assert pure_reexport_modules(mm.trees()) == sorted(FACADES), "list a new re-export facade in FACADES"


def test_the_facade_check_detects_every_forbidden_form():
    facade = Facade("kriya.foundation.x", "P1", "r")
    planted = mm.parse_sources({
        "good.py": '"""Facade; removed in P1."""\nfrom kriya.foundation.x import a as a\n',
        "logic.py": '"""Facade; removed in P1."""\nfrom kriya.foundation.x import a as a\nb = a + 1\n',
        "alias.py": '"""Facade; removed in P1."""\nfrom kriya.foundation.x import a\n',
        "other.py": '"""Facade; removed in P1."""\nfrom kriya.elsewhere import a as a\n',
        "noslice.py": '"""Facade."""\nfrom kriya.foundation.x import a as a\n',
    })
    assert facade_problems("good.py", planted["good.py"], facade) == []
    assert facade_problems("logic.py", planted["logic.py"], facade) == [
        "logic.py: line 3: not `from kriya.foundation.x import X as X`"]
    assert facade_problems("alias.py", planted["alias.py"], facade) == [
        "alias.py: line 2: a is not re-exported as itself"]
    assert facade_problems("other.py", planted["other.py"], facade) == [
        "other.py: line 2: not `from kriya.foundation.x import X as X`"]
    assert facade_problems("noslice.py", planted["noslice.py"], facade) == [
        "noslice.py: docstring does not name removal slice P1"]
    assert pure_reexport_modules(planted) == ["good.py", "noslice.py", "other.py"]
